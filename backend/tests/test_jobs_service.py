import datetime as dt
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import JobNotFoundError
from app.models import Job, JobStatus
from app.services import jobs

pytestmark = pytest.mark.integration

STALE_AFTER = dt.timedelta(minutes=15)


def now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


async def test_create_job_starts_queued(session: AsyncSession) -> None:
    job, created = await jobs.create_job(session, "octocat")

    assert created
    assert job.status == JobStatus.QUEUED
    assert job.created_at is not None  # Server default, loaded on insert.
    assert (await jobs.get_job(session, job.id)).username == "octocat"


async def test_second_active_job_is_refused_by_the_database(session: AsyncSession) -> None:
    first, _ = await jobs.create_job(session, "octocat")

    second, created = await jobs.create_job(session, "octocat")

    assert not created
    assert second.id == first.id


async def test_finished_jobs_do_not_block_new_ones(session: AsyncSession) -> None:
    first, _ = await jobs.create_job(session, "octocat")
    await jobs.finish_job(session, first.id, now=now())

    second, created = await jobs.create_job(session, "octocat")

    assert created
    assert second.id != first.id


async def test_find_active_job(session: AsyncSession) -> None:
    job, _ = await jobs.create_job(session, "octocat")

    found = await jobs.find_active_job(session, "octocat", now=now(), stale_after=STALE_AFTER)

    assert found is not None
    assert found.id == job.id
    assert await jobs.find_active_job(session, "hubot", now=now(), stale_after=STALE_AFTER) is None


async def test_stale_jobs_are_failed_and_ignored(session: AsyncSession) -> None:
    old = Job(
        username="octocat",
        status=JobStatus.FETCHING,
        created_at=now() - dt.timedelta(hours=1),
    )
    session.add(old)
    await session.commit()

    found = await jobs.find_active_job(session, "octocat", now=now(), stale_after=STALE_AFTER)

    assert found is None
    await session.refresh(old)
    assert old.status == JobStatus.FAILED
    assert old.error == jobs.STALE_JOB_MESSAGE
    assert old.finished_at is not None


async def test_finish_job_records_outcome(session: AsyncSession) -> None:
    done, _ = await jobs.create_job(session, "octocat")
    failed, _ = await jobs.create_job(session, "hubot")

    await jobs.finish_job(session, done.id, now=now())
    await jobs.finish_job(session, failed.id, now=now(), error="Nope.")

    await session.refresh(done)
    await session.refresh(failed)
    assert (done.status, done.error) == (JobStatus.DONE, None)
    assert (failed.status, failed.error) == (JobStatus.FAILED, "Nope.")
    assert done.finished_at is not None


async def test_unknown_job(session: AsyncSession) -> None:
    with pytest.raises(JobNotFoundError):
        await jobs.get_job(session, uuid.uuid4())
