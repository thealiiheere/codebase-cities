"""POST /api/cities/{username} and GET /api/jobs/{job_id}, against a real database.

GitHub and the job queue are replaced with in-memory fakes through FastAPI's dependency
overrides, so these tests exercise routing, validation, services, and SQL together.
"""

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_github, get_queue, get_session
from app.github.errors import RateLimitExceededError
from app.models import GithubUser, Job, JobStatus
from app.services.ingestion import run_ingestion_job
from tests.fakes import FakeGitHub, FakeQueue, make_profile, make_repo


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@pytest.fixture
def fake_github() -> FakeGitHub:
    return FakeGitHub(profile=make_profile(), repos=[make_repo("city", 1001)])


@pytest.fixture
def fake_queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def api(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    fake_github: FakeGitHub,
    fake_queue: FakeQueue,
) -> FastAPI:
    async def real_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = real_session
    app.dependency_overrides[get_github] = lambda: fake_github
    app.dependency_overrides[get_queue] = lambda: fake_queue
    return app


async def jobs_in_db(session: AsyncSession) -> list[Job]:
    return list((await session.scalars(select(Job).order_by(Job.created_at))).all())


# --- POST /api/cities/{username} ---------------------------------------------------------------


@pytest.mark.integration
async def test_new_user_gets_a_queued_job(
    api: FastAPI, client: httpx.AsyncClient, fake_queue: FakeQueue, session: AsyncSession
) -> None:
    response = await client.post("/api/cities/OctoCat")

    assert response.status_code == 202
    body = response.json()
    assert body["username"] == "octocat"
    assert body["status"] == "queued"
    assert response.headers["location"] == f"/api/jobs/{body['id']}"
    assert fake_queue.enqueued == [uuid.UUID(body["id"])]
    assert [job.username for job in await jobs_in_db(session)] == ["octocat"]


@pytest.mark.integration
async def test_repeat_request_reuses_the_running_job(
    api: FastAPI, client: httpx.AsyncClient, fake_queue: FakeQueue, fake_github: FakeGitHub
) -> None:
    first = await client.post("/api/cities/octocat")
    second = await client.post("/api/cities/octocat")

    assert second.status_code == 202
    assert second.json()["id"] == first.json()["id"]
    assert len(fake_queue.enqueued) == 1
    assert fake_github.calls == ["lookup_user"]  # The second request didn't ask GitHub again.


@pytest.mark.integration
async def test_unknown_github_user_is_404(
    api: FastAPI, client: httpx.AsyncClient, fake_queue: FakeQueue, session: AsyncSession
) -> None:
    response = await client.post("/api/cities/ghost")

    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "github_user_not_found",
            "message": "GitHub user 'ghost' does not exist.",
        }
    }
    assert fake_queue.enqueued == []
    assert await jobs_in_db(session) == []


@pytest.mark.integration
async def test_rate_limit_is_503_with_retry_after(
    api: FastAPI, client: httpx.AsyncClient, fake_github: FakeGitHub
) -> None:
    fake_github.error = RateLimitExceededError("core", utcnow() + dt.timedelta(seconds=120))

    response = await client.post("/api/cities/octocat")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "github_rate_limited"
    assert 100 <= int(response.headers["retry-after"]) <= 121


@pytest.mark.integration
async def test_missing_github_token_is_503(api: FastAPI, client: httpx.AsyncClient) -> None:
    api.dependency_overrides[get_github] = lambda: None

    response = await client.post("/api/cities/octocat")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "github_unavailable"


@pytest.mark.integration
async def test_queue_outage_is_503_and_does_not_block_retries(
    api: FastAPI, client: httpx.AsyncClient, fake_queue: FakeQueue, session: AsyncSession
) -> None:
    fake_queue.error = ConnectionError("redis down")

    response = await client.post("/api/cities/octocat")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "queue_unavailable"
    [job] = await jobs_in_db(session)
    assert job.status == JobStatus.FAILED

    fake_queue.error = None
    retry = await client.post("/api/cities/octocat")
    assert retry.status_code == 202


@pytest.mark.integration
async def test_fresh_data_is_200_without_asking_github(
    api: FastAPI, client: httpx.AsyncClient, fake_github: FakeGitHub, session: AsyncSession
) -> None:
    fetched_at = utcnow() - dt.timedelta(hours=1)
    session.add(user_row(fetched_at))
    await session.commit()

    response = await client.post("/api/cities/octocat")

    assert response.status_code == 200
    assert response.json()["username"] == "octocat"
    assert dt.datetime.fromisoformat(response.json()["fetched_at"]) == fetched_at
    assert fake_github.calls == []


@pytest.mark.integration
async def test_outdated_data_starts_a_new_job(
    api: FastAPI, client: httpx.AsyncClient, session: AsyncSession
) -> None:
    session.add(user_row(utcnow() - dt.timedelta(days=2)))
    await session.commit()

    response = await client.post("/api/cities/octocat")

    assert response.status_code == 202


@pytest.mark.integration
async def test_stale_job_is_replaced(
    api: FastAPI, client: httpx.AsyncClient, fake_queue: FakeQueue, session: AsyncSession
) -> None:
    stuck = Job(
        username="octocat", status=JobStatus.FETCHING, created_at=utcnow() - dt.timedelta(hours=1)
    )
    session.add(stuck)
    await session.commit()

    response = await client.post("/api/cities/octocat")

    assert response.status_code == 202
    assert response.json()["id"] != str(stuck.id)
    await session.refresh(stuck)
    assert stuck.status == JobStatus.FAILED


def user_row(fetched_at: dt.datetime) -> GithubUser:
    return GithubUser(
        github_id=583231,
        login="octocat",
        name="The Octocat",
        avatar_url="https://avatars.example/1",
        created_at=dt.datetime(2011, 1, 25, tzinfo=dt.UTC),
        fetched_at=fetched_at,
    )


# --- GET /api/jobs/{job_id} --------------------------------------------------------------------


@pytest.mark.integration
async def test_poll_a_job(api: FastAPI, client: httpx.AsyncClient) -> None:
    created = (await client.post("/api/cities/octocat")).json()

    response = await client.get(f"/api/jobs/{created['id']}")

    assert response.status_code == 200
    assert response.json() == created


@pytest.mark.integration
async def test_unknown_job_is_404(api: FastAPI, client: httpx.AsyncClient) -> None:
    response = await client.get(f"/api/jobs/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "job_not_found"


# --- The whole flow ----------------------------------------------------------------------------


@pytest.mark.integration
async def test_request_ingest_poll_then_fresh(
    api: FastAPI,
    client: httpx.AsyncClient,
    fake_github: FakeGitHub,
    fake_queue: FakeQueue,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_id = (await client.post("/api/cities/octocat")).json()["id"]

    # Do what the worker would do with the enqueued job.
    await run_ingestion_job(
        fake_queue.enqueued[0], session_factory=session_factory, github=fake_github, max_repos=100
    )

    polled = (await client.get(f"/api/jobs/{job_id}")).json()
    assert polled["status"] == "done"
    assert polled["finished_at"] is not None
    again = await client.post("/api/cities/octocat")
    assert again.status_code == 200


# --- Validation (no database needed) -----------------------------------------------------------


@pytest.fixture
def offline_api(app: FastAPI) -> FastAPI:
    async def no_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_session] = no_session
    app.dependency_overrides[get_github] = lambda: None
    app.dependency_overrides[get_queue] = lambda: FakeQueue()
    return app


@pytest.mark.parametrize("username", ["-bad", "bad-", "dou--ble", "under_score", "x" * 40])
async def test_invalid_usernames_are_422(
    offline_api: FastAPI, client: httpx.AsyncClient, username: str
) -> None:
    response = await client.post(f"/api/cities/{username}")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["message"].startswith("path.username:")


async def test_malformed_job_id_is_422(offline_api: FastAPI, client: httpx.AsyncClient) -> None:
    response = await client.get("/api/jobs/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
