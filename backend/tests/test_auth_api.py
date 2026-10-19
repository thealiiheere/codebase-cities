"""Sign in with GitHub through the API, from leaving for GitHub to signing out.

GitHub's side is mocked with respx (never the real GitHub), and sessions live in an in-memory
store, so these tests need no database or Redis.
"""

from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from fastapi import FastAPI
from pydantic import SecretStr

from app.api.deps import get_oauth, get_session, get_session_store
from app.config import Settings
from app.github.oauth import GitHubOAuth
from app.sessions import SESSION_COOKIE, SIGN_IN_COOKIE
from tests.fakes import FakeSessionStore

OAUTH_URL = "https://github.test"
API_URL = "https://api.github.test"
TOKEN_URL = f"{OAUTH_URL}/login/oauth/access_token"
USER_URL = f"{API_URL}/user"

USER = {
    "id": 7,
    "login": "Hubot",
    "name": "Hubot Robot",
    "avatar_url": "https://avatars.test/7",
    "created_at": "2012-01-01T00:00:00Z",
    "type": "User",
}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    # The shared test settings, with sign-in configured.
    return settings.model_copy(
        update={
            "github_oauth_client_id": "client-id",
            "github_oauth_client_secret": SecretStr("client-secret"),
            "public_url": "http://test",
        }
    )


@pytest.fixture
def sessions() -> FakeSessionStore:
    return FakeSessionStore()


@pytest.fixture
async def oauth(settings: Settings, respx_mock: respx.MockRouter) -> AsyncIterator[GitHubOAuth]:
    async with GitHubOAuth(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri=settings.oauth_redirect_uri,
        oauth_url=OAUTH_URL,
        api_url=API_URL,
    ) as client:
        yield client


@pytest.fixture(autouse=True)
def wired(app: FastAPI, oauth: GitHubOAuth, sessions: FakeSessionStore) -> None:
    async def no_database() -> AsyncIterator[None]:
        # Private repos are off here (no TOKEN_ENCRYPTION_KEY), so nothing touches the database;
        # test_private_repos.py covers them against a real one.
        yield None

    app.dependency_overrides[get_oauth] = lambda: oauth
    app.dependency_overrides[get_session_store] = lambda: sessions
    app.dependency_overrides[get_session] = no_database


@pytest.fixture
def github(respx_mock: respx.MockRouter) -> respx.MockRouter:
    """GitHub accepting any code."""
    respx_mock.post(TOKEN_URL).respond(json={"access_token": "gho_secret", "scope": ""})
    respx_mock.get(USER_URL).respond(json=USER)
    return respx_mock


def set_cookies(response: httpx.Response) -> dict[str, str]:
    """Each Set-Cookie header of a response, by cookie name."""
    headers = response.headers.get_list("set-cookie")
    return {header.split("=", 1)[0]: header for header in headers}


async def sign_in(client: httpx.AsyncClient, next_path: str | None = None) -> httpx.Response:
    params = {"next": next_path} if next_path else {}
    login = await client.get("/api/auth/login", params=params)
    state = parse_qs(urlparse(login.headers["location"]).query)["state"][0]
    return await client.get("/api/auth/callback", params={"code": "the-code", "state": state})


# --- Signing in --------------------------------------------------------------------------------


async def test_login_sends_the_visitor_to_github(
    client: httpx.AsyncClient, sessions: FakeSessionStore
) -> None:
    response = await client.get("/api/auth/login", params={"next": "/city/octocat"})

    assert response.status_code == 302
    location = urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        f"{OAUTH_URL}/login/oauth/authorize"
    )
    query = parse_qs(location.query)
    assert query["redirect_uri"] == ["http://test/api/auth/callback"]
    # The browser keeps the state in a short-lived cookie, only sent back to /api/auth.
    state = query["state"][0]
    cookie = set_cookies(response)[SIGN_IN_COOKIE]
    assert cookie.startswith(f"{SIGN_IN_COOKIE}={state};")
    for flag in ("HttpOnly", "Max-Age=600", "Path=/api/auth", "SameSite=lax"):
        assert flag in cookie
    assert "Secure" not in cookie  # The site is on plain http here.
    assert sessions.sign_ins == {state: "/city/octocat"}


async def test_signing_in_starts_a_session_and_returns_to_the_page(
    client: httpx.AsyncClient, sessions: FakeSessionStore, github: respx.MockRouter
) -> None:
    response = await sign_in(client, "/city/octocat")

    assert response.status_code == 302
    assert response.headers["location"] == "/city/octocat"
    cookies = set_cookies(response)
    assert "HttpOnly" in cookies[SESSION_COOKIE]
    assert "Path=/;" in cookies[SESSION_COOKIE]
    assert "Max-Age=0" in cookies[SIGN_IN_COOKIE]  # Done with it.

    me = await client.get("/api/auth/me")
    assert me.json() == {
        "enabled": True,
        "user": {"login": "Hubot", "name": "Hubot Robot", "avatar_url": "https://avatars.test/7"},
        "private_repos": None,  # Not set up on this server.
    }
    # Only the public profile is kept: the GitHub token is used once and dropped.
    [user] = sessions.sessions.values()
    assert "gho_secret" not in user.model_dump_json()


async def test_signing_in_from_the_home_page_lands_on_your_city(
    client: httpx.AsyncClient, github: respx.MockRouter
) -> None:
    response = await sign_in(client)

    assert response.headers["location"] == "/city/hubot"


@pytest.mark.parametrize("next_path", ["//evil.test/x", "https://evil.test", "/\\evil.test"])
async def test_next_can_never_point_to_another_site(
    client: httpx.AsyncClient, github: respx.MockRouter, next_path: str
) -> None:
    response = await sign_in(client, next_path)

    assert response.headers["location"] == "/city/hubot"


async def test_cookies_are_secure_on_https(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings
) -> None:
    app.state.settings = settings.model_copy(update={"public_url": "https://cities.test"})

    response = await client.get("/api/auth/login")

    assert "Secure" in set_cookies(response)[SIGN_IN_COOKIE]


# --- Sign-ins that fail ------------------------------------------------------------------------


async def test_cancelling_on_github_goes_back_with_an_error(
    client: httpx.AsyncClient, sessions: FakeSessionStore, respx_mock: respx.MockRouter
) -> None:
    await client.get("/api/auth/login")
    [state] = sessions.sign_ins

    response = await client.get(
        "/api/auth/callback", params={"error": "access_denied", "state": state}
    )

    assert response.headers["location"] == "/?auth_error=denied"
    assert SESSION_COOKIE not in set_cookies(response)
    assert not respx_mock.calls  # GitHub was never asked.


async def test_a_sign_in_started_in_another_browser_is_refused(
    client: httpx.AsyncClient, sessions: FakeSessionStore, github: respx.MockRouter
) -> None:
    # Someone starts a sign-in and tricks this browser into finishing it. It never got that
    # sign-in's cookie, so it isn't signed in as them.
    state = await sessions.begin_sign_in("")

    response = await client.get("/api/auth/callback", params={"code": "c", "state": state})

    assert response.headers["location"] == "/?auth_error=state"
    assert sessions.sessions == {}


async def test_an_expired_sign_in_is_refused(
    client: httpx.AsyncClient, sessions: FakeSessionStore, github: respx.MockRouter
) -> None:
    await client.get("/api/auth/login")
    [state] = sessions.sign_ins
    sessions.sign_ins.clear()  # Expired in the store.

    response = await client.get("/api/auth/callback", params={"code": "c", "state": state})

    assert response.headers["location"] == "/?auth_error=expired"


async def test_a_code_github_refuses_goes_back_with_an_error(
    client: httpx.AsyncClient, sessions: FakeSessionStore, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(TOKEN_URL).respond(json={"error": "bad_verification_code"})

    response = await sign_in(client)

    assert response.headers["location"] == "/?auth_error=github"
    assert sessions.sessions == {}


# --- Who is signed in, and signing out ---------------------------------------------------------


async def test_nobody_is_signed_in_at_first(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/auth/me")

    assert response.json() == {"enabled": True, "user": None, "private_repos": None}


async def test_signing_out_ends_the_session(
    client: httpx.AsyncClient, sessions: FakeSessionStore, github: respx.MockRouter
) -> None:
    await sign_in(client)

    response = await client.post("/api/auth/logout")

    assert response.status_code == 204
    assert "Max-Age=0" in set_cookies(response)[SESSION_COOKIE]
    assert sessions.sessions == {}
    assert (await client.get("/api/auth/me")).json()["user"] is None


async def test_an_unavailable_session_store_means_signed_out(
    client: httpx.AsyncClient, sessions: FakeSessionStore
) -> None:
    sessions.error = ConnectionError("redis down")
    client.cookies.set(SESSION_COOKIE, "session-1")

    response = await client.get("/api/auth/me")

    assert response.status_code == 200
    assert response.json()["user"] is None


async def test_without_an_oauth_app_there_is_no_sign_in(
    app: FastAPI, client: httpx.AsyncClient, settings: Settings
) -> None:
    app.state.settings = settings.model_copy(
        update={"github_oauth_client_id": None, "github_oauth_client_secret": None}
    )
    app.dependency_overrides[get_oauth] = lambda: None

    me = await client.get("/api/auth/me")
    login = await client.get("/api/auth/login")

    assert me.json() == {"enabled": False, "user": None, "private_repos": None}
    assert login.status_code == 404
    assert login.json()["error"]["code"] == "auth_disabled"
