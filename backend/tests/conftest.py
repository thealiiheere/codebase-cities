import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import Settings
from app.db import create_session_factory
from app.github.client import GitHubClient
from app.github.transport import GitHubTransport
from app.main import create_app
from app.models import Base
from tests.github_fakes import FakeTime

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.fixture
def settings() -> Settings:
    # _env_file=None ignores the developer's .env so tests behave the same everywhere.
    # Port 1 is never open, so anything that accidentally connects fails fast instead of
    # touching a real database or Redis.
    return Settings(
        _env_file=None,
        environment="test",
        database_url="postgresql+asyncpg://test:test@127.0.0.1:1/test",
        redis_url="redis://127.0.0.1:1/0",
    )


@pytest.fixture
def app(settings: Settings) -> FastAPI:
    return create_app(settings)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    # ASGITransport calls the app in-process: no server, no network.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.fixture
def alembic_config() -> Config:
    return Config(str(BACKEND_DIR / "alembic.ini"))


# --- GitHub client -----------------------------------------------------------------------------


@pytest.fixture
def fake_time() -> FakeTime:
    return FakeTime()


@pytest.fixture
async def github_transport(
    fake_time: FakeTime, respx_mock: respx.MockRouter
) -> AsyncIterator[GitHubTransport]:
    # Depending on respx_mock means every GitHub test runs with HTTP mocked: any request that
    # has no matching mocked route fails instead of reaching the real API.
    async with GitHubTransport(
        "test-token",
        max_retries=3,
        rate_limit_reserve=50,
        max_wait_seconds=60,
        sleep=fake_time.sleep,
        clock=fake_time.now,
    ) as transport:
        yield transport


@pytest.fixture
def github(github_transport: GitHubTransport) -> GitHubClient:
    return GitHubClient(github_transport, max_commits_per_repo=5000)


# --- Real PostgreSQL and Redis (integration tests) ---------------------------------------------
# These need TEST_DATABASE_URL / TEST_REDIS_URL pointing at disposable servers; every test
# depending on them is skipped otherwise. CI always provides both. The data is wiped after
# each test, so never point these at a database or Redis you care about.


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


@pytest.fixture(scope="session")
def migrated_database_url(test_database_url: str) -> str:
    """The test database with every migration applied, once per test session."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", test_database_url)
    command.upgrade(config, "head")
    return test_database_url


@pytest.fixture
async def db_engine(migrated_database_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(migrated_database_url)
    yield engine
    # Empty every table so each test starts clean. RESTART IDENTITY resets id counters.
    tables = ", ".join(Base.metadata.tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    await engine.dispose()


@pytest.fixture
def session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return create_session_factory(db_engine)


@pytest.fixture
async def session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session


@pytest.fixture(scope="session")
def test_redis_url() -> str:
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL is not set")
    return url


@pytest.fixture
async def redis_client(test_redis_url: str) -> AsyncIterator[Redis]:
    redis = Redis.from_url(test_redis_url, decode_responses=True)
    yield redis
    await redis.flushdb()
    await redis.aclose()
