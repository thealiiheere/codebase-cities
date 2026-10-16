"""Per-client limits on requests that spend the server's GitHub quota.

Services depend on the RateLimiter protocol; Redis is the implementation, so every API process
shares one count per client.
"""

import ipaddress
from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis

from app.config import Settings

KEY_PREFIX = "ratelimit:new-city:"


@dataclass(frozen=True)
class RateLimitDecision:
    allowed: bool
    # Seconds until the client may try again. Only set when the request is not allowed.
    retry_after_seconds: int = 0


class RateLimiter(Protocol):
    async def hit(self, client_ip: str) -> RateLimitDecision:
        """Count one request from this client and say whether it may go ahead."""
        ...


def client_bucket(client_ip: str) -> str:
    """The name a client's requests are counted under.

    IPv4 addresses count one by one. An IPv6 user usually has a whole /64 network to themselves
    (2^64 addresses), so IPv6 counts per /64; otherwise changing address would dodge the limit.
    """
    try:
        ip = ipaddress.ip_address(client_ip)
    except ValueError:
        return client_ip  # Not an IP address (e.g. a test client's name): count it as given.
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:  # ::ffff:1.2.3.4 is just an IPv4 client.
            return str(ip.ipv4_mapped)
        return str(ipaddress.IPv6Network((ip, 64), strict=False))
    return str(ip)


class RedisRateLimiter:
    """A fixed window per client: the first request opens a window of `window_seconds`, and up
    to `limit` requests fit in it. One round trip per request. The trade-off of fixed windows is
    that a client can fit up to 2 x limit requests around the end of one window and the start
    of the next, which is fine for protecting an hourly quota."""

    def __init__(self, redis: Redis, *, limit: int, window_seconds: int) -> None:
        self._redis = redis
        self._limit = limit
        self._window = window_seconds

    async def hit(self, client_ip: str) -> RateLimitDecision:
        key = KEY_PREFIX + client_bucket(client_ip)
        # One MULTI/EXEC transaction. SET NX opens a window (and sets its expiry) only when none
        # is open; INCR counts this request and keeps that expiry; TTL says when the window
        # ends. Creating the counter and its expiry together means a crash can never leave a
        # counter without one, which would block the client for good. Refused requests count
        # too but don't extend the window, so a client retrying in a loop is still let back in
        # when it ends.
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.set(key, 0, ex=self._window, nx=True)
            pipe.incr(key)
            pipe.ttl(key)
            _, count, ttl = await pipe.execute()
        if count <= self._limit:
            return RateLimitDecision(allowed=True)
        # TTL rounds down (0 in the window's last second); never tell a client to wait 0 seconds.
        return RateLimitDecision(allowed=False, retry_after_seconds=max(int(ttl), 1))


def create_rate_limiter(redis: Redis, settings: Settings) -> RedisRateLimiter | None:
    # None when the limit is turned off (RATE_LIMIT_NEW_CITIES=0).
    if settings.rate_limit_new_cities == 0:
        return None
    return RedisRateLimiter(
        redis,
        limit=settings.rate_limit_new_cities,
        window_seconds=settings.rate_limit_window_seconds,
    )
