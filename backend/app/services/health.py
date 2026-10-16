"""Dependency checks behind the health endpoint."""

import asyncio
import logging
from dataclasses import dataclass

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Without a timeout, an unreachable Postgres can hang a check for asyncpg's 60s connect timeout.
CHECK_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class HealthReport:
    database: bool
    redis: bool

    @property
    def failing(self) -> list[str]:
        return [name for name, ok in (("database", self.database), ("redis", self.redis)) if not ok]

    @property
    def healthy(self) -> bool:
        return not self.failing


async def check_database(session: AsyncSession) -> bool:
    # Any failure (refused connection, auth, timeout) means "not healthy", so catch broadly.
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await session.execute(text("SELECT 1"))
    except Exception:
        logger.warning("Database health check failed", exc_info=True)
        return False
    return True


async def check_redis(redis: Redis) -> bool:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await redis.ping()
    except Exception:
        logger.warning("Redis health check failed", exc_info=True)
        return False
    return True


async def check_health(session: AsyncSession, redis: Redis) -> HealthReport:
    database_ok, redis_ok = await asyncio.gather(check_database(session), check_redis(redis))
    return HealthReport(database=database_ok, redis=redis_ok)
