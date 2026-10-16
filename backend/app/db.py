"""Async SQLAlchemy engine and session factory."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    # pool_pre_ping replaces connections that Postgres dropped while they sat idle in the pool.
    return create_async_engine(settings.database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False keeps loaded attributes usable after commit, which avoids
    # surprise lazy loads (not allowed in async code) when building responses.
    return async_sessionmaker(engine, expire_on_commit=False)
