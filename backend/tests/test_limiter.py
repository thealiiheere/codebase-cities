"""The rate limit on requests that spend GitHub quota: the policy (no Redis needed), then the
Redis limiter against a real Redis (integration)."""

import asyncio
import logging

import pytest
from redis.asyncio import Redis

from app.cache import create_redis
from app.config import Settings
from app.errors import RateLimitedError
from app.limiter import (
    KEY_PREFIX,
    RedisRateLimiter,
    Requester,
    client_bucket,
    create_rate_limiter,
)
from app.services.cities import check_rate_limit
from tests.fakes import FakeRateLimiter

VISITOR = Requester("203.0.113.7")

# --- Who counts as the same client -------------------------------------------------------------


@pytest.mark.parametrize(
    ("client_ip", "bucket"),
    [
        ("203.0.113.7", "203.0.113.7"),
        # Every address in one IPv6 /64 is one client, however it's written.
        ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
        ("2001:0db8:0001:0002:ffff:ffff:ffff:ffff", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.7", "203.0.113.7"),  # An IPv4 client seen through IPv6.
        ("unknown", "unknown"),
    ],
)
def test_client_bucket(client_ip: str, bucket: str) -> None:
    assert client_bucket(client_ip) == bucket


def test_neighbouring_ipv6_networks_are_different_clients() -> None:
    assert client_bucket("2001:db8:1:2::1") != client_bucket("2001:db8:1:3::1")


def test_signed_in_visitors_are_counted_by_account_wherever_they_are() -> None:
    assert Requester("203.0.113.7", github_id=42).bucket == "user:42"
    assert Requester("198.51.100.1", github_id=42).bucket == "user:42"
    assert VISITOR.bucket == "203.0.113.7"


# --- Settings ----------------------------------------------------------------------------------


def test_limit_defaults() -> None:
    settings = Settings(_env_file=None)

    assert (settings.rate_limit_new_cities, settings.rate_limit_window_seconds) == (10, 3600)
    assert settings.rate_limit_new_cities_signed_in == 30


async def test_zero_turns_the_limit_off(settings: Settings) -> None:
    redis = create_redis(settings)  # Connects lazily, and nothing here makes it connect.

    assert isinstance(create_rate_limiter(redis, settings), RedisRateLimiter)
    off = settings.model_copy(update={"rate_limit_new_cities": 0})
    assert create_rate_limiter(redis, off) is None
    await redis.aclose()


# --- The policy: what a refused or failing limiter means for a request -------------------------


async def test_allowed_requests_pass() -> None:
    limiter = FakeRateLimiter(limit=1)

    await check_rate_limit(limiter, VISITOR)

    assert limiter.hits == ["203.0.113.7"]


async def test_no_limiter_means_no_limit() -> None:
    await check_rate_limit(None, VISITOR)


@pytest.mark.parametrize(
    ("seconds", "wait"),
    [(1, "1 minute"), (60, "1 minute"), (61, "2 minutes"), (3600, "60 minutes")],
)
async def test_refused_requests_raise_429_with_retry_after(seconds: int, wait: str) -> None:
    limiter = FakeRateLimiter(limit=0, retry_after_seconds=seconds)

    with pytest.raises(RateLimitedError) as caught:
        await check_rate_limit(limiter, VISITOR)

    error = caught.value
    assert (error.status_code, error.code) == (429, "rate_limited")
    assert error.headers == {"Retry-After": str(seconds)}
    assert error.message.endswith(f"Please try again in {wait}.")


async def test_signed_in_visitors_are_told_it_is_their_account() -> None:
    with pytest.raises(RateLimitedError) as caught:
        await check_rate_limit(FakeRateLimiter(limit=0), Requester("203.0.113.7", github_id=42))

    assert caught.value.message.startswith("Too many new cities requested from your account.")


async def test_a_failing_limiter_allows_the_request(caplog: pytest.LogCaptureFixture) -> None:
    limiter = FakeRateLimiter(error=ConnectionError("redis down"))

    with caplog.at_level(logging.WARNING, logger="app.services.cities"):
        await check_rate_limit(limiter, VISITOR)

    assert "Rate limiter unavailable" in caplog.text


# --- The Redis limiter -------------------------------------------------------------------------


def redis_limiter(redis: Redis, limit: int, signed_in_limit: int = 100) -> RedisRateLimiter:
    return RedisRateLimiter(redis, limit=limit, signed_in_limit=signed_in_limit, window_seconds=600)


@pytest.mark.integration
async def test_allows_the_limit_then_refuses_until_the_window_ends(redis_client: Redis) -> None:
    limiter = redis_limiter(redis_client, 3)

    decisions = [await limiter.hit(Requester("203.0.113.7")) for _ in range(5)]

    assert [d.allowed for d in decisions] == [True, True, True, False, False]
    assert all(0 < d.retry_after_seconds <= 600 for d in decisions[3:])
    assert 0 < await redis_client.ttl(KEY_PREFIX + "203.0.113.7") <= 600


@pytest.mark.integration
async def test_clients_are_counted_separately(redis_client: Redis) -> None:
    limiter = redis_limiter(redis_client, 1)

    assert (await limiter.hit(Requester("203.0.113.7"))).allowed
    assert not (await limiter.hit(Requester("203.0.113.7"))).allowed
    assert (await limiter.hit(Requester("198.51.100.1"))).allowed
    # A new address in the same IPv6 /64 is the same client.
    assert (await limiter.hit(Requester("2001:db8:1:2::1"))).allowed
    assert not (await limiter.hit(Requester("2001:db8:1:2::99"))).allowed


@pytest.mark.integration
async def test_refused_requests_do_not_extend_the_window(redis_client: Redis) -> None:
    limiter = redis_limiter(redis_client, 1)
    await limiter.hit(Requester("203.0.113.7"))
    await redis_client.expire(KEY_PREFIX + "203.0.113.7", 30)  # As if the window were ending.

    refused = await limiter.hit(Requester("203.0.113.7"))

    assert not refused.allowed
    assert 0 < refused.retry_after_seconds <= 30


@pytest.mark.integration
async def test_a_new_window_opens_after_the_old_one_expires(redis_client: Redis) -> None:
    limiter = redis_limiter(redis_client, 1)
    await limiter.hit(Requester("203.0.113.7"))
    assert not (await limiter.hit(Requester("203.0.113.7"))).allowed

    await redis_client.pexpire(KEY_PREFIX + "203.0.113.7", 1)
    await asyncio.sleep(0.05)

    assert (await limiter.hit(Requester("203.0.113.7"))).allowed
    assert 0 < await redis_client.ttl(KEY_PREFIX + "203.0.113.7") <= 600


@pytest.mark.integration
async def test_signed_in_visitors_have_their_own_higher_limit(redis_client: Redis) -> None:
    limiter = redis_limiter(redis_client, limit=1, signed_in_limit=2)
    signed_in = Requester("203.0.113.7", github_id=42)

    assert (await limiter.hit(VISITOR)).allowed
    assert not (await limiter.hit(VISITOR)).allowed
    # Signing in on the same address starts a separate, larger count for the account...
    assert [(await limiter.hit(signed_in)).allowed for _ in range(3)] == [True, True, False]
    # ...which follows the account to other addresses.
    assert not (await limiter.hit(Requester("198.51.100.1", github_id=42))).allowed
    assert await redis_client.ttl(KEY_PREFIX + "user:42") > 0
