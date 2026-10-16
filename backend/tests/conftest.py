import os
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from alembic.config import Config
from fastapi import FastAPI

from app.config import Settings
from app.main import create_app

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
def test_database_url() -> str:
    """URL of a disposable Postgres database. Tests using it are skipped when it is unset."""
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set")
    return url
