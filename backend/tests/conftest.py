import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import respx
from alembic.config import Config
from fastapi import FastAPI

from app.config import Settings
from app.github.client import GitHubClient
from app.github.transport import GitHubTransport
from app.main import create_app
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


@pytest.fixture
def test_database_url() -> str:
    """URL of a disposable Postgres database. Tests using it are skipped when it is unset."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url
