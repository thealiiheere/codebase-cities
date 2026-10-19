"""Private repos against a real database: storing them apart from public ones, the encrypted
tokens and their renewal, the private ingestion job, and who the API shows them to.

GitHub is faked (and the token endpoint mocked with respx): never the real GitHub.
"""

import datetime as dt
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import (
    get_city_cache,
    get_oauth,
    get_queue,
    get_rate_limiter,
    get_session,
    get_session_store,
    get_token_cipher,
)
from app.config import Settings
from app.errors import PrivateAccessError
from app.github.oauth import GitHubOAuth, UserToken
from app.ingestion.fetch import FetchedPrivateRepos, FetchedUserData
from app.ingestion.store import delete_private_repos, store_private_repos, store_user_data
from app.models import GithubUser, Job, JobStatus, Repo, StoredToken
from app.services.city_data import load_user_data
from app.services.ingestion import run_private_ingestion_job
from app.services.user_tokens import SIGN_IN_AGAIN, keep_token, usable_access_token
from app.sessions import SESSION_COOKIE
from app.token_cipher import TokenCipher
from tests.fakes import (
    FakeCityCache,
    FakePrivateGitHub,
    FakeQueue,
    FakeRateLimiter,
    FakeSessionStore,
    make_profile,
    make_repo,
)

pytestmark = pytest.mark.integration

NOW = dt.datetime.now(dt.UTC)
HUBOT = make_profile("Hubot", github_id=7)
CIPHER = TokenCipher("a long random string")
TOKEN_URL = "https://github.test/login/oauth/access_token"


async def store_public(
    session_factory: async_sessionmaker[AsyncSession], *names: str, profile: object = HUBOT
) -> int:
    data = FetchedUserData(
        profile=profile,  # type: ignore[arg-type]
        repos=[make_repo(name, 1000 + i) for i, name in enumerate(names)],
        calendar=[],
        histories={},
    )
    async with session_factory() as session, session.begin():
        return await store_user_data(session, data, fetched_at=NOW)


async def store_private(
    session_factory: async_sessionmaker[AsyncSession], user_id: int, *names: str
) -> None:
    data = FetchedPrivateRepos(
        login="Hubot",
        repos=[make_repo(name, 2000 + i) for i, name in enumerate(names)],
        histories={},
    )
    async with session_factory() as session, session.begin():
        await store_private_repos(session, user_id, data, fetched_at=NOW)


async def repo_names(session: AsyncSession, *, private: bool) -> set[str]:
    rows = await session.scalars(select(Repo.name).where(Repo.is_private.is_(private)))
    return set(rows)


# --- Storing -----------------------------------------------------------------------------------


async def test_public_and_private_refreshes_never_delete_each_other(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    user_id = await store_public(session_factory, "open")
    await store_private(session_factory, user_id, "secret")
    await store_public(session_factory, "open", "open-2")  # A later public refresh...
    await store_private(session_factory, user_id, "secret", "secret-2")  # ...and private one.

    assert await repo_names(session, private=False) == {"open", "open-2"}
    assert await repo_names(session, private=True) == {"secret", "secret-2"}


async def test_private_repos_are_only_loaded_on_request(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    user_id = await store_public(session_factory, "open")
    await store_private(session_factory, user_id, "secret")

    public = await load_user_data(session, "hubot")
    full = await load_user_data(session, "hubot", include_private=True)

    assert public is not None and full is not None
    assert [r.name for r in public.data.repos] == ["open"]
    assert {(r.name, r.private) for r in full.data.repos} == {("open", False), ("secret", True)}


async def test_forgetting_private_repos_keeps_the_public_ones(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    user_id = await store_public(session_factory, "open")
    await store_private(session_factory, user_id, "secret")

    async with session_factory() as other, other.begin():
        await delete_private_repos(other, HUBOT.github_id)

    assert await repo_names(session, private=False) == {"open"}
    assert await repo_names(session, private=True) == set()
    user = await session.get(GithubUser, user_id)
    assert user is not None and user.private_fetched_at is None


# --- Tokens ------------------------------------------------------------------------------------


@pytest.fixture
async def oauth(respx_mock: respx.MockRouter) -> AsyncIterator[GitHubOAuth]:
    async with GitHubOAuth(
        client_id="id", client_secret="secret", redirect_uri="x", oauth_url="https://github.test"
    ) as client:
        yield client


async def test_tokens_are_stored_encrypted(session: AsyncSession) -> None:
    await keep_token(session, CIPHER, 7, UserToken("ghu_access", refresh_token="ghr_refresh"))

    stored = await session.get(StoredToken, 7)
    assert stored is not None
    assert "ghu_access" not in stored.access_token
    assert stored.refresh_token is not None and "ghr_refresh" not in stored.refresh_token
    assert await usable_access_token(session, CIPHER, None, 7, now=NOW) == "ghu_access"


async def test_an_expired_token_is_renewed_and_the_new_pair_kept(
    session: AsyncSession, oauth: GitHubOAuth, respx_mock: respx.MockRouter
) -> None:
    expired = UserToken(
        "ghu_old",
        access_expires_at=NOW - dt.timedelta(minutes=1),
        refresh_token="ghr_old",
        refresh_expires_at=NOW + dt.timedelta(days=30),
    )
    await keep_token(session, CIPHER, 7, expired)
    route = respx_mock.post(TOKEN_URL).respond(
        json={"access_token": "ghu_new", "expires_in": 28800, "refresh_token": "ghr_new"}
    )

    assert await usable_access_token(session, CIPHER, oauth, 7, now=NOW) == "ghu_new"
    assert route.called
    session.expire_all()
    stored = await session.get(StoredToken, 7)
    assert stored is not None and stored.refresh_token is not None
    assert CIPHER.decrypt(stored.refresh_token) == "ghr_new"  # Refresh tokens work only once.


@pytest.mark.parametrize(
    "token",
    [
        None,  # Never stored.
        UserToken(  # Both expired.
            "a",
            access_expires_at=NOW - dt.timedelta(days=1),
            refresh_token="r",
            refresh_expires_at=NOW - dt.timedelta(seconds=1),
        ),
        UserToken("a", access_expires_at=NOW - dt.timedelta(days=1)),  # Nothing to renew with.
    ],
)
async def test_without_a_usable_token_the_user_must_sign_in_again(
    session: AsyncSession, token: UserToken | None
) -> None:
    if token is not None:
        await keep_token(session, CIPHER, 7, token)

    with pytest.raises(PrivateAccessError, match=SIGN_IN_AGAIN):
        await usable_access_token(session, CIPHER, None, 7, now=NOW)


async def test_tokens_from_an_old_encryption_key_are_unusable(session: AsyncSession) -> None:
    await keep_token(session, TokenCipher("the old key"), 7, UserToken("ghu_access"))

    with pytest.raises(PrivateAccessError):
        await usable_access_token(session, CIPHER, None, 7, now=NOW)


# --- The private ingestion job -----------------------------------------------------------------


async def private_job(session: AsyncSession, username: str = "hubot") -> Job:
    job = Job(username=username, status=JobStatus.QUEUED, private=True)
    session.add(job)
    await session.commit()
    return job


async def test_private_job_fetches_with_the_users_own_token(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    await store_public(session_factory, "open")
    await keep_token(session, CIPHER, HUBOT.github_id, UserToken("ghu_hubot"))
    job = await private_job(session)
    made: list[FakePrivateGitHub] = []

    def github_for(token: str) -> FakePrivateGitHub:
        made.append(FakePrivateGitHub("Hubot", [make_repo("secret", 2001)], token=token))
        return made[-1]

    cache = FakeCityCache()
    await run_private_ingestion_job(
        job.id,
        session_factory=session_factory,
        cipher=CIPHER,
        oauth=None,
        github_for=github_for,
        max_repos=100,
        city_cache=cache,
    )

    await session.refresh(job)
    assert (job.status, job.error) == (JobStatus.DONE, None)
    assert [(client.token, client.closed) for client in made] == [("ghu_hubot", True)]
    assert await repo_names(session, private=True) == {"secret"}
    [cached] = cache.entries.values()
    assert json.loads(cached)["private_repos"] == 1


async def test_private_job_without_a_token_asks_to_sign_in_again(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    await store_public(session_factory, "open")
    job = await private_job(session)

    await run_private_ingestion_job(
        job.id,
        session_factory=session_factory,
        cipher=CIPHER,
        oauth=None,
        github_for=lambda token: FakePrivateGitHub("Hubot", []),
        max_repos=100,
    )

    await session.refresh(job)
    assert (job.status, job.error) == (JobStatus.FAILED, SIGN_IN_AGAIN)


async def test_private_job_refuses_a_token_of_another_account(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    await store_public(session_factory, "open")
    await keep_token(session, CIPHER, HUBOT.github_id, UserToken("ghu_x"))
    job = await private_job(session)

    await run_private_ingestion_job(
        job.id,
        session_factory=session_factory,
        cipher=CIPHER,
        oauth=None,
        github_for=lambda token: FakePrivateGitHub("someone-else", [make_repo("theirs", 3001)]),
        max_repos=100,
    )

    await session.refresh(job)
    assert job.status == JobStatus.FAILED
    assert await repo_names(session, private=True) == set()


# --- The API: private repos only ever reach their owner ----------------------------------------


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "github_oauth_client_id": "client-id",
            "github_oauth_client_secret": SecretStr("client-secret"),
            "github_app_slug": "codebase-cities",
            "token_encryption_key": SecretStr("a long random string"),
        }
    )


@pytest.fixture
def sessions() -> FakeSessionStore:
    return FakeSessionStore()


@pytest.fixture
def queue() -> FakeQueue:
    return FakeQueue()


@pytest.fixture
def api(
    app: FastAPI,
    session_factory: async_sessionmaker[AsyncSession],
    sessions: FakeSessionStore,
    queue: FakeQueue,
) -> FastAPI:
    async def real_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = real_session
    app.dependency_overrides[get_session_store] = lambda: sessions
    app.dependency_overrides[get_queue] = lambda: queue
    app.dependency_overrides[get_city_cache] = lambda: FakeCityCache()
    app.dependency_overrides[get_rate_limiter] = lambda: FakeRateLimiter()
    app.dependency_overrides[get_token_cipher] = lambda: CIPHER
    app.dependency_overrides[get_oauth] = lambda: None
    return app


async def sign_in_as(
    client: httpx.AsyncClient, sessions: FakeSessionStore, profile: object = HUBOT
) -> None:
    client.cookies.set(SESSION_COOKIE, await sessions.create(profile))  # type: ignore[arg-type]


async def with_private_city(session_factory: async_sessionmaker[AsyncSession]) -> None:
    user_id = await store_public(session_factory, "open")
    await store_private(session_factory, user_id, "secret")


def building_names(response: httpx.Response) -> set[str]:
    return {b["repo"] for b in response.json()["city"]["buildings"]}


async def test_the_public_city_never_shows_private_repos(
    api: FastAPI,
    client: httpx.AsyncClient,
    sessions: FakeSessionStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await with_private_city(session_factory)
    await sign_in_as(client, sessions)  # Even for the owner, unless they ask.

    response = await client.get("/api/cities/hubot")

    assert building_names(response) == {"open"}
    assert response.json()["private_repos"] == 0


async def test_owners_see_their_private_repos(
    api: FastAPI,
    client: httpx.AsyncClient,
    sessions: FakeSessionStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await with_private_city(session_factory)
    await sign_in_as(client, sessions)

    response = await client.get("/api/cities/hubot", params={"private": "true"})

    assert building_names(response) == {"open", "secret"}
    assert response.json()["private_repos"] == 1
    assert response.headers["cache-control"] == "private, no-store"
    timeline = await client.get(
        "/api/cities/hubot", params={"private": "true", "until": "2025-01-01"}
    )
    assert timeline.status_code == 200


@pytest.mark.parametrize("visitor", [None, make_profile("someone", github_id=99)])
async def test_nobody_else_sees_private_repos(
    api: FastAPI,
    client: httpx.AsyncClient,
    sessions: FakeSessionStore,
    session_factory: async_sessionmaker[AsyncSession],
    visitor: object,
) -> None:
    await with_private_city(session_factory)
    if visitor is not None:
        await sign_in_as(client, sessions, visitor)

    response = await client.get("/api/cities/hubot", params={"private": "true"})

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "private_city_forbidden"


async def test_asking_for_private_repos_needs_a_sign_in(
    api: FastAPI, client: httpx.AsyncClient
) -> None:
    response = await client.post("/api/me/private-city")

    assert response.status_code == 401


async def test_asking_without_a_stored_token_needs_a_new_sign_in(
    api: FastAPI,
    client: httpx.AsyncClient,
    sessions: FakeSessionStore,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await store_public(session_factory, "open")
    await sign_in_as(client, sessions)

    response = await client.post("/api/me/private-city")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "private_access_needed"


async def test_asking_starts_one_private_job(
    api: FastAPI,
    client: httpx.AsyncClient,
    sessions: FakeSessionStore,
    queue: FakeQueue,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
) -> None:
    await store_public(session_factory, "open")
    await keep_token(session, CIPHER, HUBOT.github_id, UserToken("ghu_x"))
    await sign_in_as(client, sessions)

    first = await client.post("/api/me/private-city")
    second = await client.post("/api/me/private-city")

    assert first.status_code == second.status_code == 202
    assert first.json()["id"] == second.json()["id"]
    assert len(queue.private_enqueued) == 1
    assert queue.enqueued == []  # Not a public job.


async def test_fresh_private_repos_come_back_right_away(
    api: FastAPI,
    client: httpx.AsyncClient,
    sessions: FakeSessionStore,
    queue: FakeQueue,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
) -> None:
    await with_private_city(session_factory)
    await keep_token(session, CIPHER, HUBOT.github_id, UserToken("ghu_x"))
    await sign_in_as(client, sessions)

    response = await client.post("/api/me/private-city")
    refreshed = await client.post("/api/me/private-city", params={"refresh": "true"})

    assert response.status_code == 200
    assert building_names(response) == {"open", "secret"}
    assert response.headers["cache-control"] == "private, no-store"
    assert refreshed.status_code == 202  # Asked to fetch again anyway.


async def test_signing_out_forgets_the_token_and_private_repos(
    api: FastAPI,
    client: httpx.AsyncClient,
    sessions: FakeSessionStore,
    session_factory: async_sessionmaker[AsyncSession],
    session: AsyncSession,
) -> None:
    await with_private_city(session_factory)
    await keep_token(session, CIPHER, HUBOT.github_id, UserToken("ghu_x"))
    await sign_in_as(client, sessions)

    response = await client.post("/api/auth/logout")

    assert response.status_code == 204
    assert await session.get(StoredToken, HUBOT.github_id) is None
    assert await repo_names(session, private=True) == set()
    assert await repo_names(session, private=False) == {"open"}


async def test_signed_in_users_get_the_link_to_share_private_repos(
    api: FastAPI, client: httpx.AsyncClient, sessions: FakeSessionStore
) -> None:
    await sign_in_as(client, sessions)

    status = (await client.get("/api/auth/me")).json()

    assert status["private_repos"] == {
        "install_url": "https://github.com/apps/codebase-cities/installations/new"
    }


async def test_signing_in_keeps_the_token_encrypted(
    api: FastAPI,
    client: httpx.AsyncClient,
    sessions: FakeSessionStore,
    session: AsyncSession,
    respx_mock: respx.MockRouter,
) -> None:
    async with GitHubOAuth(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="http://test/api/auth/callback",
        oauth_url="https://github.test",
        api_url="https://api.github.test",
    ) as oauth:
        api.dependency_overrides[get_oauth] = lambda: oauth
        respx_mock.post(TOKEN_URL).respond(json={"access_token": "ghu_new"})
        respx_mock.get("https://api.github.test/user").respond(
            json={
                "id": 7,
                "login": "Hubot",
                "avatar_url": "https://avatars.test/7",
                "created_at": "2012-01-01T00:00:00Z",
                "type": "User",
            }
        )
        login = await client.get("/api/auth/login")
        [state] = sessions.sign_ins
        assert login.status_code == 302

        await client.get("/api/auth/callback", params={"code": "c", "state": state})

    stored = await session.get(StoredToken, 7)
    assert stored is not None
    assert CIPHER.decrypt(stored.access_token) == "ghu_new"
