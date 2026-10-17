"""Sign in with GitHub: the OAuth web flow for a GitHub OAuth App.

1. The visitor is sent to GitHub's authorize page, with a random `state` we remember.
2. GitHub sends them back to our callback with a one-time `code` and that same state.
3. We trade the code for an access token and use it once, to ask GitHub who they are.

The token is then dropped: we only need to know who signed in, never to act as them. No scopes
are requested, so the token could only read public information anyway.
"""

from types import TracebackType
from typing import Self
from urllib.parse import urlencode

import httpx
from pydantic import ValidationError

from app.config import Settings
from app.github.errors import GitHubError
from app.github.models import UserProfile
from app.github.payloads import RestUser


class OAuthError(GitHubError):
    """The sign-in failed: an expired or reused code, or GitHub misbehaving or unreachable."""


class GitHubOAuth:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        oauth_url: str = "https://github.com",
        api_url: str = "https://api.github.com",
        timeout_seconds: float = 15,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._oauth_url = oauth_url.rstrip("/")
        self._api_url = api_url.rstrip("/")
        self._http = httpx.AsyncClient(
            timeout=timeout_seconds, headers={"User-Agent": "codebase-cities"}
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        if settings.github_oauth_client_id is None or settings.github_oauth_client_secret is None:
            raise ValueError("Sign-in needs GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET.")
        return cls(
            client_id=settings.github_oauth_client_id,
            client_secret=settings.github_oauth_client_secret.get_secret_value(),
            redirect_uri=settings.oauth_redirect_uri,
            oauth_url=settings.github_oauth_url,
            api_url=settings.github_api_url,
        )

    def authorize_url(self, state: str) -> str:
        """GitHub's page where the visitor approves signing in."""
        query = urlencode(
            {"client_id": self._client_id, "redirect_uri": self._redirect_uri, "state": state}
        )
        return f"{self._oauth_url}/login/oauth/authorize?{query}"

    async def sign_in(self, code: str) -> UserProfile:
        """Who the visitor is, from the code GitHub sent back. Raises OAuthError."""
        token = await self._exchange(code)
        return await self._user(token)

    async def _exchange(self, code: str) -> str:
        response = await self._send(
            "POST",
            f"{self._oauth_url}/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "redirect_uri": self._redirect_uri,
            },
        )
        body = _json_object(response)
        token = body.get("access_token")
        if not isinstance(token, str) or not token:
            # An expired or reused code still gets a 200, with {"error": ..., "error_description"}.
            raise OAuthError(str(body.get("error_description") or "GitHub refused the code."))
        return token

    async def _user(self, token: str) -> UserProfile:
        response = await self._send(
            "GET",
            f"{self._api_url}/user",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            return RestUser.model_validate(_json_object(response)).to_profile()
        except ValidationError as exc:
            raise OAuthError("GitHub sent an unexpected user profile.") from exc

    async def _send(
        self, method: str, url: str, *, headers: dict[str, str], data: dict[str, str] | None = None
    ) -> httpx.Response:
        try:
            response = await self._http.request(method, url, headers=headers, data=data)
        except httpx.HTTPError as exc:
            raise OAuthError("GitHub could not be reached.") from exc
        if response.status_code != httpx.codes.OK:
            raise OAuthError(f"GitHub answered {response.status_code}.")
        return response

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()


def _json_object(response: httpx.Response) -> dict[str, object]:
    try:
        body = response.json()
    except ValueError as exc:
        raise OAuthError("GitHub sent a response that isn't JSON.") from exc
    if not isinstance(body, dict):
        raise OAuthError("GitHub sent an unexpected response.")
    return body
