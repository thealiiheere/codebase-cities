"""FastAPI app factory.

Run with: uvicorn app.main:create_app --factory
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import api_router
from app.cache import RedisEtagStore, create_redis
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.errors import register_error_handlers
from app.github.client import create_github_client
from app.queue import create_job_queue


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Shared clients are created once per process and reached through app.state by the
        # dependencies in app/api/deps.py. None of them connects until first use.
        engine = create_engine(resolved)
        redis = create_redis(resolved)
        queue = create_job_queue(resolved)
        # Without a token the API still serves health checks and job status; requests that
        # need GitHub get a clear 503 instead.
        github = (
            create_github_client(resolved, RedisEtagStore(redis)) if resolved.github_token else None
        )
        app.state.session_factory = create_session_factory(engine)
        app.state.redis = redis
        app.state.queue = queue
        app.state.github = github
        try:
            yield
        finally:
            if github is not None:
                await github.aclose()
            await queue.aclose()
            await redis.aclose()
            await engine.dispose()

    app = FastAPI(title="Codebase Cities", version="0.1.0", lifespan=lifespan)
    # Set outside the lifespan so it is available even in tests that skip startup.
    app.state.settings = resolved
    register_error_handlers(app)
    app.include_router(api_router, prefix="/api")
    return app
