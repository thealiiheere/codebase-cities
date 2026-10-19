"""Sign in with GitHub: the web flow for a GitHub App (an OAuth App works the same way).

1. The visitor is sent to GitHub's authorize page, with a random `state` we remember.
2. GitHub sends them back to our callback with a one-time `code` and that same state.
3. We trade the code for a user access token and ask GitHub who they are.

A GitHub App's token can only do what the app's permissions allow (here: read-only access to
the repos the user chose to share), and expires after a few hours; the refresh token that comes
with it gets a new one. What happens to the tokens is up to the caller (see
app/services/user_tokens.py).
"""

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass
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


@dataclass(frozen=True)
class UserToken:
    """A user's access token, and the refresh token that renews it (expiry times in UTC).

    Without "expire user authorization tokens" in the app's settings, GitHub sends no expiry and
    no refresh token: the access token then lasts until the user revokes it.
    """

    access_token: str
    access_expires_at: dt.datetime | None = None
    refresh_token: str | None = None
    refresh_expires_at: dt.datetime | None = None


@dataclass(frozen=True)
class SignedInUser:
    profile: UserProfile
    token: UserToken


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


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
        clock: Callable[[], dt.datetime] = _utcnow,
    ) -> None:
        self._clock = clock
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

    async def sign_in(self, code: str) -> SignedInUser:
        """Who the visitor is, and their tokens, from the code GitHub sent back.

        Raises OAuthError.
        """
        token = await self._token(
            {"code": code, "redirect_uri": self._redirect_uri}, refused="GitHub refused the code."
        )
        return SignedInUser(profile=await self._user(token.access_token), token=token)

    async def refresh(self, refresh_token: str) -> UserToken:
        """New tokens for an expired access token. Raises OAuthError (e.g. when the refresh token
        expired too, or the user revoked the app)."""
        return await self._token(
            {"grant_type": "refresh_token", "refresh_token": refresh_token},
            refused="GitHub refused to renew the sign-in.",
        )

    async def _token(self, form: dict[str, str], *, refused: str) -> UserToken:
        now = self._clock()
        response = await self._send(
            "POST",
            f"{self._oauth_url}/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={"client_id": self._client_id, "client_secret": self._client_secret, **form},
        )
        body = _json_object(response)
        access = body.get("access_token")
        if not isinstance(access, str) or not access:
            # A bad code or refresh token still gets a 200, with {"error", "error_description"}.
            raise OAuthError(str(body.get("error_description") or refused))
        refresh = body.get("refresh_token")
        return UserToken(
            access_token=access,
            access_expires_at=_expiry(now, body.get("expires_in")),
            refresh_token=refresh if isinstance(refresh, str) and refresh else None,
            refresh_expires_at=_expiry(now, body.get("refresh_token_expires_in")),
        )

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


def _expiry(now: dt.datetime, seconds: object) -> dt.datetime | None:
    if isinstance(seconds, int) and seconds > 0:
        return now + dt.timedelta(seconds=seconds)
    return None


def _json_object(response: httpx.Response) -> dict[str, object]:
    try:
        body = response.json()
    except ValueError as exc:
        raise OAuthError("GitHub sent a response that isn't JSON.") from exc
    if not isinstance(body, dict):
        raise OAuthError("GitHub sent an unexpected response.")
    return body
