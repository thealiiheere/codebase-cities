"""Job bookkeeping in the `jobs` table: creating, deduplicating, and moving through statuses."""

import datetime as dt
import uuid

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import JobNotFoundError
from app.models import Job, JobStatus
from app.models.job import ACTIVE_JOB_STATUSES

STALE_JOB_MESSAGE = "This job stopped responding. Please try again."


async def get_job(session: AsyncSession, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    if job is None:
        raise JobNotFoundError(f"No job with id {job_id}.")
    return job


async def find_active_job(
    session: AsyncSession, username: str, *, now: dt.datetime, stale_after: dt.timedelta
) -> Job | None:
    """The user's unfinished job, if any. Jobs that look dead are marked failed first."""
    await session.execute(
        update(Job)
        .where(
            Job.username == username,
            Job.status.in_(ACTIVE_JOB_STATUSES),
            Job.created_at < now - stale_after,
        )
        .values(status=JobStatus.FAILED, error=STALE_JOB_MESSAGE, finished_at=now)
    )
    await session.commit()
    return await _active_job(session, username)


async def create_job(session: AsyncSession, username: str) -> tuple[Job, bool]:
    """Create a queued job. Returns (job, created).

    If another request created an active job for this user in the meantime, the unique index
    rejects our insert and we return theirs with created=False, so only one gets enqueued.
    """
    job = Job(username=username, status=JobStatus.QUEUED)
    session.add(job)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        existing = await _active_job(session, username)
        if existing is None:
            raise
        return existing, False
    return job, True


async def set_status(session: AsyncSession, job_id: uuid.UUID, status: JobStatus) -> None:
    await session.execute(update(Job).where(Job.id == job_id).values(status=status))
    await session.commit()


async def finish_job(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    now: dt.datetime,
    error: str | None = None,
) -> None:
    """Mark a job done, or failed when an error message is given."""
    status = JobStatus.FAILED if error else JobStatus.DONE
    await session.execute(
        update(Job).where(Job.id == job_id).values(status=status, error=error, finished_at=now)
    )
    await session.commit()


async def _active_job(session: AsyncSession, username: str) -> Job | None:
    return await session.scalar(
        select(Job).where(Job.username == username, Job.status.in_(ACTIVE_JOB_STATUSES))
    )
