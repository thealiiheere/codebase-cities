"""Handling a request for someone's city: serve fresh data, reuse a running job, or start one."""

import datetime as dt
import logging
import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import GitHubUnavailableError, QueueUnavailableError, RateLimitedError
from app.ingestion.fetch import GitHubSource
from app.limiter import RateLimiter, Requester
from app.models import GithubUser, Job
from app.queue import JobQueue
from app.services import jobs
from app.services.github_errors import github_errors_as_app_errors
from app.usernames import normalize_login

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CityReady:
    """Stored data is fresh. (Phase 5 adds the cached city itself.)"""

    username: str
    fetched_at: dt.datetime


@dataclass(frozen=True)
class CityQueued:
    """An ingestion job is queued or running."""

    job: Job


async def request_city(
    session: AsyncSession,
    username: str,
    *,
    github: GitHubSource | None,
    queue: JobQueue,
    settings: Settings,
    limiter: RateLimiter | None,
    requester: Requester,
    now: dt.datetime | None = None,
) -> CityReady | CityQueued:
    now = now or dt.datetime.now(dt.UTC)
    username = normalize_login(username)

    fetched_at = await session.scalar(
        select(GithubUser.fetched_at).where(GithubUser.login == username)
    )
    fresh_for = dt.timedelta(hours=settings.refresh_after_hours)
    if fetched_at is not None and now - fetched_at < fresh_for:
        return CityReady(username=username, fetched_at=fetched_at)

    stale_after = dt.timedelta(minutes=settings.job_stale_after_minutes)
    active = await jobs.find_active_job(session, username, now=now, stale_after=stale_after)
    if active is not None:
        return CityQueued(job=active)

    # Check the user exists before queueing, so an unknown name gets a 404 right away instead
    # of a job that fails later. Repeat lookups are free thanks to ETags.
    if github is None:
        raise GitHubUnavailableError("This server has no GitHub token configured.")
    # Everything from here on spends GitHub quota (the lookup, then the ingestion job), so it
    # counts against the client's limit. Fresh cities and running jobs above never do.
    await check_rate_limit(limiter, requester)
    with github_errors_as_app_errors(now):
        await github.lookup_user(username)

    job, created = await jobs.create_job(session, username)
    if created:
        await _enqueue(session, queue, job, now)
    return CityQueued(job=job)


async def check_rate_limit(limiter: RateLimiter | None, requester: Requester) -> None:
    """Raise RateLimitedError if this requester has asked GitHub for too much lately."""
    if limiter is None:
        return
    try:
        decision = await limiter.hit(requester)
    except Exception:
        # Fail open: a Redis outage shouldn't stop people getting cities. The GitHub client's
        # own rate limit handling still keeps the token from being run dry in the meantime.
        logger.warning("Rate limiter unavailable; allowing the request", exc_info=True)
        return
    if not decision.allowed:
        seconds = decision.retry_after_seconds
        source = "account" if requester.signed_in else "address"
        raise RateLimitedError(
            f"Too many new cities requested from your {source}. "
            f"Please try again in {_minutes(seconds)}.",
            headers={"Retry-After": str(seconds)},
        )


def _minutes(seconds: int) -> str:
    minutes = max(math.ceil(seconds / 60), 1)
    return "1 minute" if minutes == 1 else f"{minutes} minutes"


async def _enqueue(session: AsyncSession, queue: JobQueue, job: Job, now: dt.datetime) -> None:
    try:
        await queue.enqueue_ingestion(job.id)
    except Exception as exc:
        # Without this, the job would sit "queued" forever and block new requests for the user.
        logger.exception("Could not enqueue job %s", job.id)
        await jobs.finish_job(session, job.id, now=now, error="Could not queue this job.")
        raise QueueUnavailableError("The job queue is unavailable. Please try again.") from exc
