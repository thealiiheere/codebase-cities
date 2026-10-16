"""ARQ worker: runs ingestion jobs in the background.

Run with: arq app.worker.WorkerSettings
"""

import uuid
from typing import Any

from arq.connections import RedisSettings

from app.cache import RedisEtagStore, create_city_cache, create_redis
from app.config import get_settings
from app.db import create_engine, create_session_factory
from app.github.client import create_github_client
from app.services.ingestion import run_ingestion_job

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


async def shutdown(ctx: dict[str, Any]) -> None:
    # ARQ calls this even when startup failed or never ran (e.g. Redis was unreachable), so
    # close only what exists; a KeyError here would hide the original error.
    if "github" in ctx:
        await ctx["github"].aclose()
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


_settings = get_settings()


class WorkerSettings:
    functions = (ingest_user,)
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(_settings.redis_url)
    max_jobs = _settings.worker_max_jobs
    job_timeout = _settings.worker_job_timeout_seconds
