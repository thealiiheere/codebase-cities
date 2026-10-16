"""Redis helpers."""

import json
from typing import Protocol, cast

from redis.asyncio import Redis

from app.config import Settings
from app.github.etag import CachedResponse

ETAG_KEY_PREFIX = "github:etag:"
# Long enough that repeat lookups stay free, short enough that abandoned keys clean themselves up.
ETAG_TTL_SECONDS = 7 * 24 * 3600


def create_redis(settings: Settings) -> Redis:
    # from_url connects lazily, so creating the client never blocks or fails at startup.
    # The cast is needed because redis-py leaves from_url's return type unannotated.
    return cast(Redis, Redis.from_url(settings.redis_url, decode_responses=True))


class RedisEtagStore:
    """GitHub REST ETags and response bodies, shared by the API and every worker."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def get(self, key: str) -> CachedResponse | None:
        raw = await self._redis.get(ETAG_KEY_PREFIX + key)
        if raw is None:
            return None
        entry = json.loads(raw)
        return CachedResponse(etag=entry["etag"], body=entry["body"])

    async def set(self, key: str, value: CachedResponse) -> None:
        raw = json.dumps({"etag": value.etag, "body": value.body})
        await self._redis.set(ETAG_KEY_PREFIX + key, raw, ex=ETAG_TTL_SECONDS)


class CityCache(Protocol):
    """Rendered city payloads (JSON text) by cache key."""

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str) -> None: ...


class RedisCityCache:
    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def get(self, key: str) -> str | None:
        value = await self._redis.get(key)
        return cast(str | None, value)

    async def set(self, key: str, value: str) -> None:
        await self._redis.set(key, value, ex=self._ttl)


def create_city_cache(redis: Redis, settings: Settings) -> RedisCityCache:
    return RedisCityCache(redis, ttl_seconds=round(settings.city_cache_ttl_hours * 3600))
