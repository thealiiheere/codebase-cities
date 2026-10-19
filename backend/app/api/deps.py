"""FastAPI dependencies that hand shared resources (created at startup) to routers."""

from collections.abc import AsyncIterator
from typing import Annotated, cast

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.cache import CityCache
from app.config import Settings
from app.github.models import UserProfile
from app.github.oauth import GitHubOAuth
from app.ingestion.fetch import GitHubSource
from app.limiter import RateLimiter, Requester
from app.queue import JobQueue
from app.services.auth import current_user
from app.sessions import SESSION_COOKIE, SessionStore
from app.token_cipher import TokenCipher


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = cast(async_sessionmaker[AsyncSession], request.app.state.session_factory)
    async with factory() as session:
        yield session


def get_redis(request: Request) -> Redis:
    return cast(Redis, request.app.state.redis)


def get_settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def get_github(request: Request) -> GitHubSource | None:
    # None when no GITHUB_TOKEN is configured; the service turns that into a clear 503.
    return cast(GitHubSource | None, request.app.state.github)


def get_queue(request: Request) -> JobQueue:
    return cast(JobQueue, request.app.state.queue)


def get_city_cache(request: Request) -> CityCache:
    return cast(CityCache, request.app.state.city_cache)


def get_rate_limiter(request: Request) -> RateLimiter | None:
    # None when the limit is turned off (RATE_LIMIT_NEW_CITIES=0).
    return cast(RateLimiter | None, request.app.state.rate_limiter)


def get_client_ip(request: Request) -> str:
    # Behind nginx this is the visitor's address, not nginx's: uvicorn's --proxy-headers takes
    # it from X-Forwarded-For, trusting only the proxies listed in docker-compose.prod.yml.
    return request.client.host if request.client else "unknown"


def get_oauth(request: Request) -> GitHubOAuth | None:
    # None when sign-in isn't configured (no GITHUB_OAUTH_CLIENT_ID / _SECRET).
    return cast(GitHubOAuth | None, request.app.state.oauth)


def get_token_cipher(request: Request) -> TokenCipher | None:
    # None when private repos aren't set up.
    return cast(TokenCipher | None, getattr(request.app.state, "token_cipher", None))


def get_session_store(request: Request) -> SessionStore:
    return cast(SessionStore, request.app.state.sessions)


async def get_current_user(
    request: Request, sessions: Annotated[SessionStore, Depends(get_session_store)]
) -> UserProfile | None:
    """The signed-in visitor, from their session cookie, or None."""
    return await current_user(sessions, request.cookies.get(SESSION_COOKIE))


def get_requester(
    client_ip: Annotated[str, Depends(get_client_ip)],
    user: Annotated[UserProfile | None, Depends(get_current_user)],
) -> Requester:
    return Requester(ip=client_ip, github_id=user.github_id if user else None)


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
GitHubDep = Annotated[GitHubSource | None, Depends(get_github)]
QueueDep = Annotated[JobQueue, Depends(get_queue)]
CityCacheDep = Annotated[CityCache, Depends(get_city_cache)]
RateLimiterDep = Annotated[RateLimiter | None, Depends(get_rate_limiter)]
ClientIpDep = Annotated[str, Depends(get_client_ip)]
OAuthDep = Annotated[GitHubOAuth | None, Depends(get_oauth)]
SessionStoreDep = Annotated[SessionStore, Depends(get_session_store)]
TokenCipherDep = Annotated[TokenCipher | None, Depends(get_token_cipher)]
CurrentUserDep = Annotated[UserProfile | None, Depends(get_current_user)]
RequesterDep = Annotated[Requester, Depends(get_requester)]
