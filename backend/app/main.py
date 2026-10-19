"""FastAPI app factory.

Run with: uvicorn app.main:create_app --factory
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import api_router
from app.cache import RedisEtagStore, create_city_cache, create_redis
from app.config import Settings, get_settings
from app.db import create_engine, create_session_factory
from app.errors import register_error_handlers
from app.github.client import create_github_client
from app.github.oauth import GitHubOAuth
from app.limiter import create_rate_limiter
from app.queue import create_job_queue
from app.sessions import create_session_store
from app.token_cipher import create_token_cipher


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
        # Without OAuth App credentials there is no sign-in; everything else works the same.
        oauth = GitHubOAuth.from_settings(resolved) if resolved.auth_enabled else None
        app.state.session_factory = create_session_factory(engine)
        app.state.redis = redis
        app.state.city_cache = create_city_cache(redis, resolved)
        app.state.rate_limiter = create_rate_limiter(redis, resolved)
        app.state.queue = queue
        app.state.github = github
        app.state.oauth = oauth
        app.state.sessions = create_session_store(redis, resolved)
        app.state.token_cipher = create_token_cipher(resolved)
        try:
            yield
        finally:
            if oauth is not None:
                await oauth.aclose()
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
