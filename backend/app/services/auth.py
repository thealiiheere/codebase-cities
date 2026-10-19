"""Signing in with GitHub: starting a sign-in, finishing it, and who is signed in.

Signing in identifies the visitor (for the "My city" shortcut and a higher rate limit). Public
cities are always built with the server's own GITHUB_TOKEN; the visitor's own token is only used
for the private repos they choose to share (see app/services/user_tokens.py).
"""

import logging
from dataclasses import dataclass

from app.errors import AuthDisabledError
from app.github.models import UserProfile
from app.github.oauth import GitHubOAuth, OAuthError, UserToken
from app.sessions import SessionStore

logger = logging.getLogger(__name__)

# Paths are short; anything longer than this isn't a page of ours.
MAX_NEXT_LENGTH = 512


class SignInError(Exception):
    """The visitor comes back from GitHub without being signed in. `code` tells the page why."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class SignInStarted:
    authorize_url: str
    state: str


@dataclass(frozen=True)
class SignedIn:
    session_id: str
    user: UserProfile
    # Their GitHub tokens, for the caller to keep (encrypted) or drop.
    token: UserToken
    # Where to send the visitor now.
    next_path: str


async def start_sign_in(
    oauth: GitHubOAuth | None, sessions: SessionStore, next_path: str | None
) -> SignInStarted:
    if oauth is None:
        raise AuthDisabledError("Sign-in with GitHub isn't set up on this server.")
    state = await sessions.begin_sign_in(safe_next_path(next_path))
    return SignInStarted(authorize_url=oauth.authorize_url(state), state=state)


async def finish_sign_in(
    oauth: GitHubOAuth | None,
    sessions: SessionStore,
    *,
    code: str | None,
    state: str | None,
    browser_state: str | None,
    denied: bool,
) -> SignedIn:
    """Check GitHub's answer and start a session. Raises SignInError.

    `browser_state` is the state this browser was given when it left for GitHub. Requiring it to
    match stops "login CSRF": someone else completing their own sign-in in your browser, which
    would leave you unknowingly signed in as them.
    """
    if oauth is None:
        raise AuthDisabledError("Sign-in with GitHub isn't set up on this server.")
    if denied:
        raise SignInError("denied")  # The visitor chose Cancel on GitHub's page.
    if not code or not state or state != browser_state:
        raise SignInError("state")
    next_path = await sessions.finish_sign_in(state)
    if next_path is None:
        raise SignInError("expired")
    try:
        signed_in = await oauth.sign_in(code)
    except OAuthError as exc:
        logger.warning("GitHub sign-in failed: %s", exc)
        raise SignInError("github") from exc
    user = signed_in.profile
    session_id = await sessions.create(user)
    # Signing in from the home page lands on your own city.
    return SignedIn(session_id, user, signed_in.token, next_path or f"/city/{user.login.lower()}")


async def current_user(sessions: SessionStore, session_id: str | None) -> UserProfile | None:
    """Who a session belongs to, or None (signed out, expired, or the store is unavailable)."""
    if not session_id:
        return None
    try:
        return await sessions.get(session_id)
    except Exception:
        # Being signed in only adds conveniences, so a Redis outage shouldn't break the page.
        logger.warning(
            "Session store unavailable; treating the visitor as signed out", exc_info=True
        )
        return None


async def sign_out(sessions: SessionStore, session_id: str | None) -> None:
    if session_id:
        await sessions.delete(session_id)


def safe_next_path(next_path: str | None) -> str:
    """A path on this site to return to after signing in, or "" for the visitor's own city.

    Only plain paths like /city/octocat are allowed. Anything that could point to another
    site ("//evil.example", "https://...", "/\\evil.example") is dropped, so the sign-in can't
    be used to send people somewhere else.
    """
    if (
        not next_path
        or next_path == "/"
        or len(next_path) > MAX_NEXT_LENGTH
        or not next_path.startswith("/")
        or next_path.startswith("//")
        or "\\" in next_path
        or any(ord(char) < 32 for char in next_path)
    ):
        return ""
    return next_path
