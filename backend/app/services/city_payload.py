"""Serving a user's city: build it from stored data, or return the cached copy."""

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import CityCache
from app.errors import CityNotFoundError
from app.mapping import MAPPING_VERSION, UserData, build_city, build_music, snapshot_at
from app.models import GithubUser
from app.schemas.cities import CityResponse, Timeline
from app.services.city_data import load_user_data
from app.usernames import normalize_login

logger = logging.getLogger(__name__)


def effective_until(until: dt.date | None, fetched_at: dt.datetime) -> dt.date | None:
    """The timeline date to show, or None for the latest city.

    A date on or after the fetch day can't show anything newer than the latest data, so it
    *is* the latest city; normalizing it keeps one cache entry instead of one per future date.
    """
    if until is None or until >= fetched_at.date():
        return None
    return until


def cache_key(
    username: str,
    fetched_at: dt.datetime,
    until: dt.date | None,
    private_fetched_at: dt.datetime | None = None,
) -> str:
    """Redis key for one rendered city.

    It includes the mapping version and the fetch times, so a new algorithm or fresh data
    produces new keys: stale cities are never served, they just expire. A city with private
    repos has its own key (with "p<time>"), so it can never be served as the public one.
    """
    moment = until.isoformat() if until else "latest"
    fetched = str(int(fetched_at.timestamp()))
    if private_fetched_at is not None:
        fetched += f":p{int(private_fetched_at.timestamp())}"
    return f"city:v{MAPPING_VERSION}:{username}:{fetched}:{moment}"


def build_payload(data: UserData, fetched_at: dt.datetime, until: dt.date | None) -> CityResponse:
    """Run the mapping engine for the latest city, or for the city as it was on `until`.

    A past city is drawn on the latest city's plots (`layout=data`), so buildings stay put
    while the timeline moves and the city visibly grows in place.
    """
    as_of = until or fetched_at.date()
    view = snapshot_at(data, until) if until else data
    return CityResponse(
        username=data.login.lower(),
        fetched_at=fetched_at,
        as_of=as_of,
        until=until,
        timeline=Timeline(start=data.created_at.date(), end=fetched_at.date()),
        private_repos=sum(repo.private for repo in data.repos),
        city=build_city(view, as_of=as_of, layout=data),
        music=build_music(view, as_of=as_of),
    )


async def get_city_json(
    session: AsyncSession,
    cache: CityCache,
    username: str,
    until: dt.date | None = None,
    *,
    include_private: bool = False,
) -> str:
    """The city for a user as JSON text, from the cache when possible.

    With `include_private` (only ever for the owner), the private repos they shared are part of
    the city too, if any were fetched. Raises CityNotFoundError when the user was never ingested.
    """
    username = normalize_login(username)
    row = (
        await session.execute(
            select(GithubUser.fetched_at, GithubUser.private_fetched_at).where(
                GithubUser.login == username
            )
        )
    ).first()
    if row is None:
        raise CityNotFoundError(
            f"No city for '{username}' yet. POST /api/cities/{username} to build it."
        )
    fetched_at, private_fetched_at = row
    with_private = include_private and private_fetched_at is not None
    until = effective_until(until, fetched_at)
    key = cache_key(username, fetched_at, until, private_fetched_at if with_private else None)

    cached = await _cache_get(cache, key)
    if cached is not None:
        return cached

    stored = await load_user_data(session, username, include_private=with_private)
    if stored is None:  # Deleted between the two queries.
        raise CityNotFoundError(f"No city for '{username}' yet.")
    body = build_payload(stored.data, stored.fetched_at, until).model_dump_json()
    await _cache_set(cache, key, body)
    return body


# The cache only speeds things up. If Redis misbehaves, log it and serve from Postgres.


async def _cache_get(cache: CityCache, key: str) -> str | None:
    try:
        return await cache.get(key)
    except Exception:
        logger.warning("City cache read failed for %s", key, exc_info=True)
        return None


async def _cache_set(cache: CityCache, key: str, body: str) -> None:
    try:
        await cache.set(key, body)
    except Exception:
        logger.warning("City cache write failed for %s", key, exc_info=True)
