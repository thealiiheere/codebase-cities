"""FastAPI app factory.

Run with: uvicorn app.main:create_app --factory
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import api_router
from app.cache import create_redis
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.errors import register_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Shared clients are created once per process and reached through app.state by the
        # dependencies in app/api/deps.py. Neither connects until first use.
        engine = create_engine(resolved)
        redis = create_redis(resolved)
        app.state.session_factory = create_session_factory(engine)
        app.state.redis = redis
        try:
            yield
        finally:
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(title="Codebase Cities", version="0.1.0", lifespan=lifespan)
    register_error_handlers(app)
    app.include_router(api_router, prefix="/api")
    return app
