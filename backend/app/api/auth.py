"""Sign in with GitHub.

GET  /api/auth/login?next=/city/x   Leave for GitHub's sign-in page.
GET  /api/auth/callback             GitHub sends the visitor back here; sets the session cookie.
GET  /api/auth/me                   Whether sign-in is available, and who is signed in.
POST /api/auth/logout               End the session.

Login and callback are visited by the browser itself (not fetched by page scripts), so they
answer with redirects. A failed sign-in goes back to the site with ?auth_error=<code>.
"""

from typing import Annotated

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from app.api.deps import (
    CurrentUserDep,
    OAuthDep,
    SessionDep,
    SessionStoreDep,
    SettingsDep,
    TokenCipherDep,
)
from app.config import Settings
from app.schemas.auth import AuthStatus, AuthUser, PrivateRepos
from app.schemas.errors import ErrorResponse
from app.services.auth import MAX_NEXT_LENGTH, SignInError, finish_sign_in, sign_out, start_sign_in
from app.services.user_tokens import forget_private_data, remember_sign_in
from app.sessions import SESSION_COOKIE, SIGN_IN_COOKIE, SIGN_IN_TTL_SECONDS

router = APIRouter(prefix="/auth", tags=["auth"])

# The sign-in cookie is only needed by the callback, so it's only sent to /api/auth.
SIGN_IN_COOKIE_PATH = "/api/auth"


@router.get(
    "/login",
    response_class=RedirectResponse,
    status_code=status.HTTP_302_FOUND,
    responses={404: {"model": ErrorResponse, "description": "Sign-in isn't set up"}},
)
async def login(
    oauth: OAuthDep,
    sessions: SessionStoreDep,
    settings: SettingsDep,
    next: Annotated[str | None, Query(max_length=MAX_NEXT_LENGTH)] = None,
) -> RedirectResponse:
    started = await start_sign_in(oauth, sessions, next)
    response = RedirectResponse(started.authorize_url, status_code=status.HTTP_302_FOUND)
    # The browser keeps the state too, so the callback can check that the sign-in it finishes
    # is the one this browser started (see finish_sign_in).
    _set_cookie(
        response, settings, SIGN_IN_COOKIE, started.state, SIGN_IN_TTL_SECONDS, SIGN_IN_COOKIE_PATH
    )
    return response


@router.get("/callback", response_class=RedirectResponse, status_code=status.HTTP_302_FOUND)
async def callback(
    request: Request,
    oauth: OAuthDep,
    sessions: SessionStoreDep,
    settings: SettingsDep,
    db: SessionDep,
    cipher: TokenCipherDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    try:
        signed_in = await finish_sign_in(
            oauth,
            sessions,
            code=code,
            state=state,
            browser_state=request.cookies.get(SIGN_IN_COOKIE),
            denied=error is not None,
        )
    except SignInError as failure:
        response = RedirectResponse(f"/?auth_error={failure.code}", status.HTTP_302_FOUND)
    else:
        # Kept (encrypted) only when the server shows private repos; otherwise dropped here.
        await remember_sign_in(db, cipher, signed_in.user.github_id, signed_in.token)
        response = RedirectResponse(signed_in.next_path, status.HTTP_302_FOUND)
        max_age = round(settings.session_ttl_days * 24 * 3600)
        _set_cookie(response, settings, SESSION_COOKIE, signed_in.session_id, max_age, "/")
    _set_cookie(response, settings, SIGN_IN_COOKIE, "", 0, SIGN_IN_COOKIE_PATH)  # Delete it.
    return response


@router.get("/me", response_model=AuthStatus)
async def me(settings: SettingsDep, user: CurrentUserDep) -> AuthStatus:
    install_url = settings.app_install_url
    private = user is not None and settings.private_repos_enabled and install_url is not None
    return AuthStatus(
        enabled=settings.auth_enabled,
        user=AuthUser.from_profile(user) if user else None,
        private_repos=PrivateRepos(install_url=install_url) if private and install_url else None,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    sessions: SessionStoreDep,
    settings: SettingsDep,
    db: SessionDep,
    cipher: TokenCipherDep,
    user: CurrentUserDep,
) -> Response:
    """End the session. Signing out also deletes the user's stored GitHub tokens and the private
    repos they shared: the server keeps nothing private once they leave."""
    await sign_out(sessions, request.cookies.get(SESSION_COOKIE))
    if user is not None and cipher is not None:
        await forget_private_data(db, user.github_id)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    _set_cookie(response, settings, SESSION_COOKIE, "", 0, "/")  # Delete it.
    return response


def _set_cookie(
    response: Response, settings: Settings, name: str, value: str, max_age: int, path: str
) -> None:
    """Set a cookie, or delete it with max_age=0.

    HttpOnly: page scripts can't read it. SameSite=Lax: other sites may link here, but their
    forms and scripts can't send requests that carry it (so they can't sign anyone out).
    Secure (when the site is on https): never sent over plain http.
    """
    response.set_cookie(
        name,
        value,
        max_age=max_age,
        path=path,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
    )
