import datetime as dt

import pytest

from app.github.errors import RateLimitExceededError
from app.github.ratelimit import RateLimiter, parse_rate_limit
from tests.github_fakes import NOW, FakeTime, rate_headers


@pytest.fixture
def limiter(fake_time: FakeTime) -> RateLimiter:
    return RateLimiter(reserve=50, max_wait_seconds=60, clock=fake_time.now)


def test_parses_headers() -> None:
    state = parse_rate_limit(rate_headers(remaining=4321, reset_in=600, resource="graphql"))

    assert state is not None
    assert state.resource == "graphql"
    assert state.remaining == 4321
    assert state.reset_at == NOW + dt.timedelta(seconds=600)


def test_missing_headers_mean_unknown_state() -> None:
    assert parse_rate_limit({}) is None
    assert parse_rate_limit({"x-ratelimit-remaining": "lots"}) is None


def test_no_delay_before_any_response(limiter: RateLimiter) -> None:
    assert limiter.delay_before_request("graphql") == 0


def test_no_delay_while_budget_is_healthy(limiter: RateLimiter) -> None:
    limiter.record(rate_headers(remaining=51, reset_in=600))

    assert limiter.delay_before_request("graphql") == 0


def test_waits_for_a_close_reset_when_budget_is_low(limiter: RateLimiter) -> None:
    limiter.record(rate_headers(remaining=50, reset_in=30))

    assert limiter.delay_before_request("graphql") == 30


def test_refuses_to_wait_for_a_distant_reset(limiter: RateLimiter) -> None:
    limiter.record(rate_headers(remaining=10, reset_in=3600))

    with pytest.raises(RateLimitExceededError) as caught:
        limiter.delay_before_request("graphql")

    assert caught.value.reset_at == NOW + dt.timedelta(hours=1)
    assert "graphql rate limit exhausted" in str(caught.value)


def test_budgets_are_tracked_per_resource(limiter: RateLimiter) -> None:
    limiter.record(rate_headers(remaining=0, reset_in=3600, resource="core"))

    assert limiter.delay_before_request("graphql") == 0


def test_retry_after_header_sets_the_wait(limiter: RateLimiter) -> None:
    assert limiter.delay_after_limited(403, {"retry-after": "12"}, "") == 12


def test_exhausted_primary_limit_waits_until_reset(limiter: RateLimiter) -> None:
    headers = rate_headers(remaining=0, reset_in=45)

    assert limiter.delay_after_limited(403, headers, "API rate limit exceeded") == 45


def test_secondary_limit_without_headers_waits_a_minute(limiter: RateLimiter) -> None:
    body = '{"message": "You have exceeded a secondary rate limit."}'

    assert limiter.delay_after_limited(403, {}, body) == 60


def test_plain_forbidden_is_not_a_rate_limit(limiter: RateLimiter) -> None:
    assert limiter.delay_after_limited(403, {}, '{"message": "Resource not accessible"}') is None


def test_too_long_retry_after_raises(limiter: RateLimiter) -> None:
    with pytest.raises(RateLimitExceededError):
        limiter.delay_after_limited(429, {"retry-after": "600"}, "")
