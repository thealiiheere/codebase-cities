"""Private repos, the parts that need no database: encrypting tokens, settings, renewing tokens,
the private repos query, and the private flag in the city. Never the real GitHub."""

import datetime as dt
from collections.abc import AsyncIterator
from urllib.parse import parse_qs

import pytest
import respx
from pydantic import SecretStr

from app.config import Settings
from app.github.client import GitHubClient
from app.github.oauth import GitHubOAuth, OAuthError, UserToken
from app.mapping import build_city
from app.services.city_payload import cache_key
from app.token_cipher import TokenCipher, TokenDecryptError, create_token_cipher
from tests.github_fakes import GRAPHQL_URL, gql, repo_node, request_query, request_variables
from tests.mapping_fakes import AS_OF, repo, user

NOW = dt.datetime(2026, 10, 19, 12, 0, tzinfo=dt.UTC)
TOKEN_URL = "https://github.test/login/oauth/access_token"


def private_settings(settings: Settings, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "github_oauth_client_id": "client-id",
        "github_oauth_client_secret": SecretStr("client-secret"),
        "github_app_slug": "codebase-cities",
        "token_encryption_key": SecretStr("a long random string"),
        **overrides,
    }
    return settings.model_copy(update=values)


# --- Encrypting tokens -------------------------------------------------------------------------


def test_tokens_round_trip_and_are_never_stored_readable() -> None:
    cipher = TokenCipher("a long random string")

    stored = cipher.encrypt("ghu_secret")

    assert "ghu_secret" not in stored
    assert cipher.decrypt(stored) == "ghu_secret"
    # Encrypting twice gives different text (a random IV), so equal tokens don't look equal.
    assert cipher.encrypt("ghu_secret") != stored


def test_another_key_cannot_read_a_token() -> None:
    stored = TokenCipher("the old key").encrypt("ghu_secret")

    with pytest.raises(TokenDecryptError):
        TokenCipher("a new key").decrypt(stored)


def test_altered_tokens_are_rejected() -> None:
    cipher = TokenCipher("a long random string")
    stored = cipher.encrypt("ghu_secret")

    with pytest.raises(TokenDecryptError):
        cipher.decrypt(stored[:-4] + "AAAA")


# --- Settings ----------------------------------------------------------------------------------


def test_private_repos_need_sign_in_an_app_slug_and_a_key(settings: Settings) -> None:
    complete = private_settings(settings)

    assert complete.private_repos_enabled
    assert complete.app_install_url == "https://github.com/apps/codebase-cities/installations/new"
    assert create_token_cipher(complete) is not None
    for missing in (
        {"github_app_slug": None},
        {"token_encryption_key": None},
        {"token_encryption_key": SecretStr("")},
        {"github_oauth_client_secret": None},
    ):
        incomplete = private_settings(settings, **missing)
        assert not incomplete.private_repos_enabled, missing
        assert create_token_cipher(incomplete) is None


# --- Renewing tokens ---------------------------------------------------------------------------


@pytest.fixture
async def oauth(respx_mock: respx.MockRouter) -> AsyncIterator[GitHubOAuth]:
    async with GitHubOAuth(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://cities.test/api/auth/callback",
        oauth_url="https://github.test",
        api_url="https://api.github.test",
        clock=lambda: NOW,
    ) as client:
        yield client


async def test_expiring_tokens_come_with_expiry_times(
    oauth: GitHubOAuth, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(TOKEN_URL).respond(
        json={
            "access_token": "ghu_new",
            "expires_in": 28800,
            "refresh_token": "ghr_new",
            "refresh_token_expires_in": 15724800,
            "token_type": "bearer",
            "scope": "",
        }
    )

    token = await oauth.refresh("ghr_old")

    assert token == UserToken(
        access_token="ghu_new",
        access_expires_at=NOW + dt.timedelta(hours=8),
        refresh_token="ghr_new",
        refresh_expires_at=NOW + dt.timedelta(seconds=15724800),
    )


async def test_refreshing_sends_the_refresh_token(
    oauth: GitHubOAuth, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(TOKEN_URL).respond(json={"access_token": "ghu_new"})

    await oauth.refresh("ghr_old")

    sent = parse_qs(route.calls.last.request.content.decode())
    assert sent["grant_type"] == ["refresh_token"]
    assert sent["refresh_token"] == ["ghr_old"]
    assert sent["client_secret"] == ["client-secret"]


async def test_a_refused_refresh_is_an_error(
    oauth: GitHubOAuth, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(TOKEN_URL).respond(json={"error": "bad_refresh_token"})

    with pytest.raises(OAuthError):
        await oauth.refresh("ghr_expired")


# --- Fetching private repos --------------------------------------------------------------------


def viewer_page(nodes: list[dict[str, object]], *, has_next: bool = False) -> dict[str, object]:
    return {
        "viewer": {
            "login": "Hubot",
            "repositories": {
                "pageInfo": {"hasNextPage": has_next, "endCursor": "c1" if has_next else None},
                "nodes": nodes,
            },
        }
    }


async def test_own_private_repos_asks_only_for_private_owned_non_forks(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(GRAPHQL_URL).mock(
        side_effect=[
            gql(viewer_page([repo_node("secret-1")], has_next=True)),
            gql(viewer_page([repo_node("secret-2")])),
        ]
    )

    login, repos = await github.get_own_private_repos(limit=100)

    assert login == "Hubot"
    assert [r.name for r in repos] == ["secret-1", "secret-2"]
    query = request_query(route.calls.last.request)
    for argument in ("viewer", "privacy: PRIVATE", "isFork: false", "ownerAffiliations: [OWNER]"):
        assert argument in query
    assert request_variables(route.calls.last.request)["after"] == "c1"


# --- Private repos in the city -----------------------------------------------------------------


def test_private_repos_are_flagged_in_the_city() -> None:
    city = build_city(user(repo("open", 1), repo("secret", 2, private=True)), as_of=AS_OF)

    assert {b.repo: b.private for b in city.buildings} == {"open": False, "secret": True}


def test_cities_with_private_repos_have_their_own_cache_keys() -> None:
    fetched = dt.datetime(2026, 10, 19, tzinfo=dt.UTC)

    public = cache_key("hubot", fetched, None)
    private = cache_key("hubot", fetched, None, private_fetched_at=fetched)

    assert public != private
    assert ":p" in private and ":p" not in public
