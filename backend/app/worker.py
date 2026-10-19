"""ARQ worker: runs ingestion jobs in the background.

Run with: arq app.worker.WorkerSettings
"""

import datetime as dt
import uuid
from typing import Any

from arq.connections import RedisSettings

from app.cache import RedisEtagStore, create_city_cache, create_redis
from app.config import get_settings
from app.db import create_engine, create_session_factory
from app.github.client import create_github_client
from app.github.oauth import GitHubOAuth
from app.services.ingestion import run_ingestion_job, run_private_ingestion_job
from app.services.jobs import finish_job
from app.token_cipher import create_token_cipher

# ARQ passes every job a `ctx` dict. startup() fills it with clients shared by all jobs in this
# worker process; in particular one GitHub client, so its rate limit tracking sees every call.


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    engine = create_engine(settings)
    redis = create_redis(settings)
    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["redis"] = redis
    ctx["session_factory"] = create_session_factory(engine)
    ctx["city_cache"] = create_city_cache(redis, settings)
    # Raises GitHubAuthError without a token, so a misconfigured worker fails loudly at startup.
    ctx["github"] = create_github_client(settings, RedisEtagStore(redis))
    # For private repos (both None when they aren't set up): decrypting stored user tokens, and
    # renewing them when they expire.
    ctx["token_cipher"] = create_token_cipher(settings)
    ctx["oauth"] = GitHubOAuth.from_settings(settings) if settings.private_repos_enabled else None


async def shutdown(ctx: dict[str, Any]) -> None:
    # ARQ calls this even when startup failed or never ran (e.g. Redis was unreachable), so
    # close only what exists; a KeyError here would hide the original error.
    if "github" in ctx:
        await ctx["github"].aclose()
    if ctx.get("oauth") is not None:
        await ctx["oauth"].aclose()
    if "redis" in ctx:
        await ctx["redis"].aclose()
    if "engine" in ctx:
        await ctx["engine"].dispose()


async def ingest_user(ctx: dict[str, Any], job_id: str) -> None:
    """The job named by app.queue.INGEST_USER_JOB."""
    await run_ingestion_job(
        uuid.UUID(job_id),
        session_factory=ctx["session_factory"],
        github=ctx["github"],
        max_repos=ctx["settings"].max_repos_per_user,
        city_cache=ctx["city_cache"],
    )


async def ingest_private(ctx: dict[str, Any], job_id: str) -> None:
    """The job named by app.queue.INGEST_PRIVATE_JOB: a user's shared private repos, fetched
    with a GitHub client made for their own token (and closed when the job ends)."""
    settings = ctx["settings"]
    cipher = ctx["token_cipher"]
    if cipher is None:  # Private repos were turned off after the job was queued.
        async with ctx["session_factory"]() as session:
            await finish_job(
                session,
                uuid.UUID(job_id),
                now=dt.datetime.now(dt.UTC),
                error="This server no longer shows private repos.",
            )
        return
    await run_private_ingestion_job(
        uuid.UUID(job_id),
        session_factory=ctx["session_factory"],
        cipher=cipher,
        oauth=ctx["oauth"],
        github_for=lambda token: create_github_client(settings, token=token),
        max_repos=settings.max_repos_per_user,
        city_cache=ctx["city_cache"],
    )


_settings = get_settings()


class WorkerSettings:
    functions = (ingest_user, ingest_private)
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = _settings.worker_max_jobs
    job_timeout = _settings.worker_job_timeout_seconds
