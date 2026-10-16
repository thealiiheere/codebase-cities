"""The ingestion job: fetch, store, and keep the job's status up to date along the way."""

import asyncio
import datetime as dt
import logging
import uuid
from collections.abc import Callable

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache import CityCache
from app.github.errors import GitHubError
from app.ingestion.fetch import GitHubSource, fetch_user_data
from app.ingestion.store import store_user_data
from app.models import Job, JobStatus
from app.models.job import ACTIVE_JOB_STATUSES
from app.services import jobs
from app.services.city_payload import get_city_json
from app.services.github_errors import failure_message

logger = logging.getLogger(__name__)

UNEXPECTED_ERROR_MESSAGE = "Something went wrong while building this city."
TIMEOUT_MESSAGE = "Building this city took too long."

Clock = Callable[[], dt.datetime]


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def run_ingestion_job(
    job_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession],
    github: GitHubSource,
    max_repos: int,
    city_cache: CityCache | None = None,
    clock: Clock = _utcnow,
) -> None:
    """Run one job. Never raises for expected failures; they end up in the job's error field.

    With a city cache, the latest city is also built and cached, so the first view is instant.
    """
    username = await _claim(session_factory, job_id)
    if username is None:
        return

    try:
        data = await fetch_user_data(github, username, max_repos=max_repos)
        async with session_factory() as session:
            await jobs.set_status(session, job_id, JobStatus.BUILDING)
        # One transaction, so readers never see half of the new data.
        async with session_factory() as session, session.begin():
            await store_user_data(session, data, fetched_at=clock())
        if city_cache is not None:
            async with session_factory() as session:
                await get_city_json(session, city_cache, username)
        error = None
    except GitHubError as exc:
        error = failure_message(exc)
    except asyncio.CancelledError:
        # ARQ cancels jobs that exceed their timeout. Record why, then let cancellation proceed.
        await _finish(session_factory, job_id, clock(), TIMEOUT_MESSAGE)
        raise
    except Exception:
        logger.exception("Ingestion job %s failed", job_id)
        error = UNEXPECTED_ERROR_MESSAGE

    await _finish(session_factory, job_id, clock(), error)


async def _claim(
    session_factory: async_sessionmaker[AsyncSession], job_id: uuid.UUID
) -> str | None:
    """Move the job to "fetching" and return its username, or None if it shouldn't run."""
    async with session_factory() as session:
        job = await session.get(Job, job_id)
        if job is None or job.status not in ACTIVE_JOB_STATUSES:
            # Unknown, or already finished (e.g. marked stale while waiting in the queue).
            logger.warning("Skipping job %s: missing or already finished", job_id)
            return None
        await jobs.set_status(session, job_id, JobStatus.FETCHING)
        return job.username


async def _finish(
    session_factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    now: dt.datetime,
    error: str | None,
) -> None:
    async with session_factory() as session:
        await jobs.finish_job(session, job_id, now=now, error=error)
