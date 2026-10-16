"""Redis helpers."""

from typing import cast

from redis.asyncio import Redis

from app.config import Settings


def create_redis(settings: Settings) -> Redis:
    # from_url connects lazily, so creating the client never blocks or fails at startup.
    # The cast is needed because redis-py leaves from_url's return type unannotated.
    return cast(Redis, Redis.from_url(settings.redis_url, decode_responses=True))
