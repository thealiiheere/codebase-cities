import asyncio
import datetime as dt

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.github.errors import GitHubApiError, RateLimitExceededError
from app.models import GithubUser, Job, JobStatus, Repo
from app.services import jobs
from app.services.github_errors import GITHUB_UNAVAILABLE_MESSAGE
from app.services.ingestion import (
    TIMEOUT_MESSAGE,
    UNEXPECTED_ERROR_MESSAGE,
    run_ingestion_job,
)
from tests.fakes import T0, FakeGitHub, make_history, make_profile, make_repo

pytestmark = pytest.mark.integration


def octocat_github(**overrides: object) -> FakeGitHub:
    fields: dict[str, object] = {
        "profile": make_profile(),
        "repos": [make_repo("city", 1001)],
        "histories": {"city": make_history("city", T0)},
    }
    fields.update(overrides)
    return FakeGitHub(**fields)  # type: ignore[arg-type]


async def new_job(session: AsyncSession, username: str = "octocat") -> Job:
    job, _ = await jobs.create_job(session, username)
    return job


async def run(
    job: Job, github: FakeGitHub, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await run_ingestion_job(
        job.id, session_factory=session_factory, github=github, max_repos=100, clock=lambda: T0
    )


async def test_successful_job_stores_data_and_finishes(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    job = await new_job(session)

    await run(job, octocat_github(), session_factory)

    await session.refresh(job)
    assert (job.status, job.error, job.finished_at) == (JobStatus.DONE, None, T0)
    assert await session.scalar(select(GithubUser.login)) == "octocat"
    assert await session.scalar(select(Repo.name)) == "city"


async def test_status_is_fetching_while_talking_to_github(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    job = await new_job(session)
    seen: list[JobStatus] = []

    async def record_status(_method: str) -> None:
        async with session_factory() as other:
            seen.append((await jobs.get_job(other, job.id)).status)

    await run(job, octocat_github(on_call=record_status), session_factory)

    assert seen == [JobStatus.FETCHING] * 4  # One per GitHub call.


@pytest.mark.parametrize(
    ("error", "expected_message"),
    [
        (RateLimitExceededError("graphql", T0), "GitHub graphql rate limit exhausted."),
        (GitHubApiError("boom", 502), GITHUB_UNAVAILABLE_MESSAGE),
        (RuntimeError("bug"), UNEXPECTED_ERROR_MESSAGE),
    ],
)
async def test_failures_are_recorded_on_the_job(
    session: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
    error: Exception,
    expected_message: str,
) -> None:
    job = await new_job(session)

    await run(job, octocat_github(error=error), session_factory)

    await session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert job.error is not None
    assert job.error.startswith(expected_message)
    assert await session.scalar(select(GithubUser)) is None


async def test_unknown_user_fails_with_a_clear_message(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    job = await new_job(session, "ghost")

    await run(job, octocat_github(), session_factory)

    await session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert job.error == "GitHub user 'ghost' does not exist."


async def test_finished_jobs_are_not_rerun(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    job = await new_job(session)
    await jobs.finish_job(session, job.id, now=T0, error="Gave up.")
    github = octocat_github()

    await run(job, github, session_factory)

    assert github.calls == []


async def test_cancelled_job_is_marked_failed(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    job = await new_job(session)

    with pytest.raises(asyncio.CancelledError):
        await run(job, octocat_github(error=asyncio.CancelledError()), session_factory)

    await session.refresh(job)
    assert (job.status, job.error) == (JobStatus.FAILED, TIMEOUT_MESSAGE)


async def test_failed_refresh_keeps_previous_data(
    session: AsyncSession, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    await run(await new_job(session), octocat_github(), session_factory)

    await run(await new_job(session), octocat_github(error=GitHubApiError("down")), session_factory)

    assert await session.scalar(select(Repo.name)) == "city"
    previous = await session.scalar(select(GithubUser.fetched_at))
    assert previous == T0
    assert isinstance(previous, dt.datetime)
