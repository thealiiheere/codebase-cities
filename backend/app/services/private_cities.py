"""A signed-in user asking for their city with the private repos they shared.

Mirrors app/services/cities.py, but the fetching happens with the user's own GitHub token (in
the background job), and the result is only ever shown to them.
"""

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.errors import CityNotFoundError, PrivateAccessError, QueueUnavailableError
from app.github.models import UserProfile
from app.limiter import RateLimiter, Requester
from app.models import GithubUser, Job
from app.queue import JobQueue
from app.services import jobs
from app.services.cities import CityQueued, CityReady, check_rate_limit
from app.services.user_tokens import SIGN_IN_AGAIN, has_token

logger = logging.getLogger(__name__)


async def request_private_city(
    session: AsyncSession,
    user: UserProfile,
    *,
    queue: JobQueue,
    settings: Settings,
    limiter: RateLimiter | None,
    requester: Requester,
    refresh: bool = False,
    now: dt.datetime | None = None,
) -> CityReady | CityQueued:
    """Serve the user's private repos if they're fresh, reuse a running job, or start one.

    `refresh` fetches again even when fresh, e.g. right after they changed which repos they share.
    Their public city must exist first (the page always builds it before asking for this).
    """
    now = now or dt.datetime.now(dt.UTC)
    row = await session.scalar(select(GithubUser).where(GithubUser.github_id == user.github_id))
    if row is None:
        raise CityNotFoundError("Build your city first, then add your private repos.")

    fresh_for = dt.timedelta(hours=settings.refresh_after_hours)
    fetched = row.private_fetched_at
    if not refresh and fetched is not None and now - fetched < fresh_for:
        return CityReady(username=row.login, fetched_at=row.fetched_at)

    stale_after = dt.timedelta(minutes=settings.job_stale_after_minutes)
    active = await jobs.find_active_job(
        session, row.login, now=now, stale_after=stale_after, private=True
    )
    if active is not None:
        return CityQueued(job=active)

    if not await has_token(session, user.github_id):
        raise PrivateAccessError(SIGN_IN_AGAIN)
    # Private jobs spend the user's own GitHub quota, not the server's, but they still keep a
    # worker busy, so they count toward the same per-account limit.
    await check_rate_limit(limiter, requester)

    job, created = await jobs.create_job(session, row.login, private=True)
    if created:
        await _enqueue(session, queue, job, now)
    return CityQueued(job=job)


async def _enqueue(session: AsyncSession, queue: JobQueue, job: Job, now: dt.datetime) -> None:
    try:
        await queue.enqueue_private_ingestion(job.id)
    except Exception as exc:
        logger.exception("Could not enqueue private job %s", job.id)
        await jobs.finish_job(session, job.id, now=now, error="Could not queue this job.")
        raise QueueUnavailableError("The job queue is unavailable. Please try again.") from exc
