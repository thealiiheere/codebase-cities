"""The city and job endpoints, against a real database.

GitHub, the job queue, the city cache, and the rate limiter are replaced with in-memory fakes
through FastAPI's dependency overrides, so these tests exercise routing, validation, services,
and SQL together.
"""

import datetime as dt
import logging
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import get_city_cache, get_github, get_queue, get_rate_limiter, get_session
from app.github.errors import RateLimitExceededError
from app.models import GithubUser, Job, JobStatus
from app.services.ingestion import run_ingestion_job
from tests.fakes import (
    FakeCityCache,
    FakeGitHub,
    FakeQueue,
    FakeRateLimiter,
    make_profile,
    make_repo,
)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


@pytest.fixture
def fake_github() -> FakeGitHub:
    return FakeGitHub(profile=make_profile(), repos=[make_repo("city", 1001)])


@pytest.fixture
def fake_queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def fake_cache() -> FakeCityCache:
    return FakeCityCache()


@pytest.fixture
def fake_limiter() -> FakeRateLimiter:
    return FakeRateLimiter()  # Allows everything unless a test sets a limit.


@pytest.fixture
def api(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    fake_github: FakeGitHub,
    fake_queue: FakeQueue,
    fake_cache: FakeCityCache,
    fake_limiter: FakeRateLimiter,
) -> FastAPI:
    async def real_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = real_session
    app.dependency_overrides[get_github] = lambda: fake_github
    app.dependency_overrides[get_queue] = lambda: fake_queue
    app.dependency_overrides[get_city_cache] = lambda: fake_cache
    app.dependency_overrides[get_rate_limiter] = lambda: fake_limiter
    return app


async def ingest(
    client: httpx.AsyncClient,
    queue: FakeQueue,
    session_factory: async_sessionmaker[AsyncSession],
    github: FakeGitHub,
    cache: FakeCityCache | None = None,
) -> None:
    """Request a city, then do what the worker would do with the enqueued job."""
    await client.post("/api/cities/octocat")
    await run_ingestion_job(
        queue.enqueued[-1],
        session_factory=session_factory,
        github=github,
        max_repos=100,
        city_cache=cache,
    )


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
async def test_missing_github_token_is_503(
    api: FastAPI, client: httpx.AsyncClient, fake_limiter: FakeRateLimiter
) -> None:
    api.dependency_overrides[get_github] = lambda: None

    response = await client.post("/api/cities/octocat")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "github_unavailable"
    assert fake_limiter.hits == []  # Nothing reached GitHub, so nothing counted.


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
    body = response.json()
    assert body["username"] == "octocat"
    assert dt.datetime.fromisoformat(body["fetched_at"]) == fetched_at
    assert body["until"] is None
    assert body["timeline"] == {"start": "2011-01-25", "end": fetched_at.date().isoformat()}
    assert body["city"]["buildings"] == []  # This user has no repos stored.
    assert body["music"]["tracks"] == []
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


# --- Rate limiting -----------------------------------------------------------------------------


@pytest.mark.integration
async def test_requests_past_the_limit_are_429_with_retry_after(
    api: FastAPI,
    client: httpx.AsyncClient,
    fake_github: FakeGitHub,
    fake_limiter: FakeRateLimiter,
    fake_queue: FakeQueue,
    session: AsyncSession,
) -> None:
    fake_limiter.limit = 1
    fake_limiter.retry_after_seconds = 541

    unknown = await client.post("/api/cities/ghost")  # Lookups of unknown users count too.
    limited = await client.post("/api/cities/octocat")

    assert unknown.status_code == 404
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "541"
    assert limited.json() == {
        "error": {
            "code": "rate_limited",
            "message": "Too many new cities requested from your address. "
            "Please try again in 10 minutes.",
        }
    }
    # Counted by the address the request came from (httpx's in-process client is 127.0.0.1).
    assert fake_limiter.hits == ["127.0.0.1", "127.0.0.1"]
    assert fake_github.calls == ["lookup_user"]  # Only for ghost; octocat never reached GitHub.
    assert fake_queue.enqueued == []
    assert await jobs_in_db(session) == []


@pytest.mark.integration
async def test_fresh_cities_and_running_jobs_are_never_limited(
    api: FastAPI,
    client: httpx.AsyncClient,
    fake_limiter: FakeRateLimiter,
    session: AsyncSession,
) -> None:
    fake_limiter.limit = 0  # Refuse anything that asks.
    session.add(user_row(utcnow() - dt.timedelta(hours=1)))
    running = Job(username="hubot", status=JobStatus.FETCHING, created_at=utcnow())
    session.add(running)
    await session.commit()

    fresh = await client.post("/api/cities/octocat")
    reused = await client.post("/api/cities/hubot")
    read = await client.get("/api/cities/octocat")
    polled = await client.get(f"/api/jobs/{running.id}")

    assert (fresh.status_code, reused.status_code, read.status_code) == (200, 202, 200)
    assert reused.json()["id"] == str(running.id)
    assert polled.status_code == 200
    assert fake_limiter.hits == []


@pytest.mark.integration
async def test_a_broken_rate_limiter_lets_requests_through(
    api: FastAPI,
    client: httpx.AsyncClient,
    fake_limiter: FakeRateLimiter,
    fake_queue: FakeQueue,
    caplog: pytest.LogCaptureFixture,
) -> None:
    fake_limiter.error = ConnectionError("redis down")

    with caplog.at_level(logging.WARNING, logger="app.services.cities"):
        response = await client.post("/api/cities/octocat")

    assert response.status_code == 202
    assert len(fake_queue.enqueued) == 1
    assert "Rate limiter unavailable" in caplog.text


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
    fake_cache: FakeCityCache,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    job_id = (await client.post("/api/cities/octocat")).json()["id"]

    # Do what the worker would do with the enqueued job, including caching the city.
    await run_ingestion_job(
        fake_queue.enqueued[0],
        session_factory=session_factory,
        github=fake_github,
        max_repos=100,
        city_cache=fake_cache,
    )

    polled = (await client.get(f"/api/jobs/{job_id}")).json()
    assert polled["status"] == "done"
    assert polled["finished_at"] is not None
    assert len(fake_cache.entries) == 1  # The worker cached the latest city.

    again = await client.post("/api/cities/octocat")
    assert again.status_code == 200
    assert [b["repo"] for b in again.json()["city"]["buildings"]] == ["city"]
    assert fake_cache.hits == 1  # Served from the worker's cache entry.


# --- GET /api/cities/{username} ----------------------------------------------------------------


@pytest.mark.integration
async def test_city_never_built_is_404(api: FastAPI, client: httpx.AsyncClient) -> None:
    response = await client.get("/api/cities/octocat")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "city_not_found"


@pytest.mark.integration
async def test_city_is_built_once_then_served_from_cache(
    api: FastAPI,
    client: httpx.AsyncClient,
    fake_github: FakeGitHub,
    fake_queue: FakeQueue,
    fake_cache: FakeCityCache,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await ingest(client, fake_queue, session_factory, fake_github)  # No caching by the worker.

    first = await client.get("/api/cities/OctoCat")
    second = await client.get("/api/cities/octocat")

    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert (fake_cache.misses, fake_cache.hits, len(fake_cache.entries)) == (1, 1, 1)
    body = first.json()
    assert body["city"]["version"] == body["music"]["version"]
    assert body["city"]["buildings"][0]["floors"] >= 1


@pytest.mark.integration
async def test_until_shows_the_city_as_it_was(
    api: FastAPI,
    client: httpx.AsyncClient,
    fake_github: FakeGitHub,
    fake_queue: FakeQueue,
    fake_cache: FakeCityCache,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    fake_github.repos = [
        make_repo("old", 1, created_at=dt.datetime(2018, 1, 1, tzinfo=dt.UTC)),
        make_repo("new", 2, created_at=dt.datetime(2022, 1, 1, tzinfo=dt.UTC)),
    ]
    await ingest(client, fake_queue, session_factory, fake_github)

    latest = (await client.get("/api/cities/octocat")).json()
    past = (await client.get("/api/cities/octocat", params={"until": "2019-12-31"})).json()

    assert sorted(b["repo"] for b in latest["city"]["buildings"]) == ["new", "old"]
    assert [b["repo"] for b in past["city"]["buildings"]] == ["old"]
    assert (past["until"], past["as_of"], past["city"]["as_of"]) == ("2019-12-31",) * 3
    assert len(fake_cache.entries) == 2  # Each timeline date is cached separately.


@pytest.mark.integration
async def test_until_at_or_after_the_fetch_day_is_the_latest_city(
    api: FastAPI,
    client: httpx.AsyncClient,
    fake_github: FakeGitHub,
    fake_queue: FakeQueue,
    fake_cache: FakeCityCache,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await ingest(client, fake_queue, session_factory, fake_github)

    latest = await client.get("/api/cities/octocat")
    future = await client.get("/api/cities/octocat", params={"until": "2999-01-01"})

    assert future.json()["until"] is None
    assert future.content == latest.content
    assert len(fake_cache.entries) == 1


@pytest.mark.integration
async def test_a_broken_cache_does_not_break_the_city(
    api: FastAPI,
    client: httpx.AsyncClient,
    fake_github: FakeGitHub,
    fake_queue: FakeQueue,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await ingest(client, fake_queue, session_factory, fake_github)
    api.dependency_overrides[get_city_cache] = lambda: FakeCityCache(error=ConnectionError("down"))

    response = await client.get("/api/cities/octocat")

    assert response.status_code == 200
    assert response.json()["city"]["buildings"]


# --- Validation (no database needed) -----------------------------------------------------------


@pytest.fixture
def offline_api(app: FastAPI) -> FastAPI:
    async def no_session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_session] = no_session
    app.dependency_overrides[get_github] = lambda: None
    app.dependency_overrides[get_queue] = lambda: FakeQueue()
    app.dependency_overrides[get_city_cache] = lambda: FakeCityCache()
    app.dependency_overrides[get_rate_limiter] = lambda: None
    return app


@pytest.mark.parametrize("username", ["-bad", "bad-", "dou--ble", "under_score", "x" * 40])
async def test_invalid_usernames_are_422(
    offline_api: FastAPI, client: httpx.AsyncClient, username: str
) -> None:
    response = await client.post(f"/api/cities/{username}")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["message"].startswith("path.username:")


@pytest.mark.parametrize("until", ["2020-13-01", "yesterday", "20200101x"])
async def test_malformed_until_is_422(
    offline_api: FastAPI, client: httpx.AsyncClient, until: str
) -> None:
    response = await client.get("/api/cities/octocat", params={"until": until})

    assert response.status_code == 422
    assert response.json()["error"]["message"].startswith("query.until:")


async def test_malformed_job_id_is_422(offline_api: FastAPI, client: httpx.AsyncClient) -> None:
    response = await client.get("/api/jobs/not-a-uuid")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
