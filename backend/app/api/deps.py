"""FastAPI dependencies that hand shared resources (created at startup) to routers."""

from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.ingestion.fetch import GitHubSource
from app.queue import JobQueue


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = cast(async_sessionmaker[AsyncSession], request.app.state.session_factory)
    async with factory() as session:
        yield session


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_github(request: Request) -> GitHubSource | None:
    # None when no GITHUB_TOKEN is configured; the service turns that into a clear 503.
    return cast(GitHubSource | None, request.app.state.github)


def get_queue(request: Request) -> JobQueue:
    return cast(JobQueue, request.app.state.queue)


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
GitHubDep = Annotated[GitHubSource | None, Depends(get_github)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]
