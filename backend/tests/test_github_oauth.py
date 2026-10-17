"""The GitHub OAuth client, against mocked GitHub endpoints (respx): never the real GitHub."""

from collections.abc import AsyncIterator
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.config import Settings
from app.github.oauth import GitHubOAuth, OAuthError

OAUTH_URL = "https://github.test"
API_URL = "https://api.github.test"
TOKEN_URL = f"{OAUTH_URL}/login/oauth/access_token"
USER_URL = f"{API_URL}/user"
REDIRECT_URI = "https://cities.test/api/auth/callback"

USER = {
    "id": 7,
    "login": "Hubot",
    "name": None,
    "avatar_url": "https://avatars.test/7",
    "created_at": "2012-01-01T00:00:00Z",
    "type": "User",
}


@pytest.fixture
async def oauth(respx_mock: respx.MockRouter) -> AsyncIterator[GitHubOAuth]:
    # Depending on respx_mock means any request without a mocked route fails instead of
    # reaching the network.
    async with GitHubOAuth(
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri=REDIRECT_URI,
        oauth_url=OAUTH_URL,
        api_url=API_URL,
    ) as client:
        yield client


def test_authorize_url_asks_for_no_scopes(oauth: GitHubOAuth) -> None:
    url = urlparse(oauth.authorize_url("the-state"))

    assert (url.scheme, url.netloc, url.path) == ("https", "github.test", "/login/oauth/authorize")
    assert parse_qs(url.query) == {
        "client_id": ["client-id"],
        "redirect_uri": [REDIRECT_URI],
        "state": ["the-state"],
    }


async def test_sign_in_trades_the_code_and_asks_who_it_is(
    oauth: GitHubOAuth, respx_mock: respx.MockRouter
) -> None:
    token = respx_mock.post(TOKEN_URL).respond(json={"access_token": "gho_x", "scope": ""})
    user = respx_mock.get(USER_URL).respond(json=USER)

    profile = await oauth.sign_in("the-code")

    assert (profile.github_id, profile.login, profile.name) == (7, "Hubot", None)
    sent = parse_qs(token.calls.last.request.content.decode())
    assert sent == {
        "client_id": ["client-id"],
        "client_secret": ["client-secret"],
        "code": ["the-code"],
        "redirect_uri": [REDIRECT_URI],
    }
    assert token.calls.last.request.headers["accept"] == "application/json"
    assert user.calls.last.request.headers["authorization"] == "Bearer gho_x"


async def test_a_refused_code_is_an_error(oauth: GitHubOAuth, respx_mock: respx.MockRouter) -> None:
    # GitHub answers 200 even when the code is wrong, expired, or already used.
    respx_mock.post(TOKEN_URL).respond(
        json={"error": "bad_verification_code", "error_description": "The code is expired."}
    )
    user = respx_mock.get(USER_URL)

    with pytest.raises(OAuthError, match=r"The code is expired."):
        await oauth.sign_in("old-code")
    assert not user.called


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json=["not", "an", "object"]),
    ],
)
async def test_unexpected_token_responses_are_errors(
    oauth: GitHubOAuth, respx_mock: respx.MockRouter, response: httpx.Response
) -> None:
    respx_mock.post(TOKEN_URL).mock(return_value=response)

    with pytest.raises(OAuthError):
        await oauth.sign_in("the-code")


async def test_github_unreachable_is_an_error(
    oauth: GitHubOAuth, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("no route"))

    with pytest.raises(OAuthError, match="could not be reached"):
        await oauth.sign_in("the-code")


@pytest.mark.parametrize(
    "response", [httpx.Response(401), httpx.Response(200, json={"login": "no id"})]
)
async def test_a_failed_profile_lookup_is_an_error(
    oauth: GitHubOAuth, respx_mock: respx.MockRouter, response: httpx.Response
) -> None:
    respx_mock.post(TOKEN_URL).respond(json={"access_token": "gho_x"})
    respx_mock.get(USER_URL).mock(return_value=response)

    with pytest.raises(OAuthError):
        await oauth.sign_in("the-code")


async def test_built_from_settings(settings: Settings) -> None:
    configured = settings.model_copy(
        update={
            "github_oauth_client_id": "client-id",
            "github_oauth_client_secret": SecretStr("client-secret"),
            "public_url": "https://cities.test",
        }
    )

    async with GitHubOAuth.from_settings(configured) as oauth:
        query = parse_qs(urlparse(oauth.authorize_url("s")).query)

    assert query["redirect_uri"] == ["https://cities.test/api/auth/callback"]
    with pytest.raises(ValueError, match="GITHUB_OAUTH_CLIENT_ID"):
        GitHubOAuth.from_settings(settings)
