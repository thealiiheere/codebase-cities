"""Signed-in sessions and sign-ins in progress, kept in Redis.

A session is a long random id that the browser keeps in an HttpOnly cookie (page scripts can't
read it). Redis maps it to the visitor's public GitHub profile, never to a GitHub token. Only a
hash of the id is stored, so someone who can read Redis still can't take over a session.
"""

import hashlib
import secrets
from typing import Protocol, cast

from redis.asyncio import Redis

from app.config import Settings
from app.github.models import UserProfile

SESSION_COOKIE = "cc_session"
# Holds a sign-in's state between leaving for GitHub and coming back (see app/api/auth.py).
SIGN_IN_COOKIE = "cc_sign_in"

SESSION_PREFIX = "session:"
SIGN_IN_PREFIX = "sign-in:"
# How long a visitor has on GitHub's page before the sign-in expires.
SIGN_IN_TTL_SECONDS = 600


class SessionStore(Protocol):
    async def create(self, user: UserProfile) -> str:
        """Start a session for this user and return its id."""
        ...

    async def get(self, session_id: str) -> UserProfile | None: ...

    async def delete(self, session_id: str) -> None: ...

    async def begin_sign_in(self, next_path: str) -> str:
        """Remember where to go after signing in, under a new random state; return the state."""
        ...

    async def finish_sign_in(self, state: str) -> str | None:
        """The path saved for this state, or None if unknown or expired. Works once per state."""
        ...


class RedisSessionStore:
    def __init__(self, redis: Redis, ttl_seconds: int) -> None:
        self._redis = redis
        self._ttl = ttl_seconds

    async def create(self, user: UserProfile) -> str:
        session_id = secrets.token_urlsafe(32)
        await self._redis.set(_session_key(session_id), user.model_dump_json(), ex=self._ttl)
        return session_id

    async def get(self, session_id: str) -> UserProfile | None:
        raw = await self._redis.get(_session_key(session_id))
        return None if raw is None else UserProfile.model_validate_json(raw)

    async def delete(self, session_id: str) -> None:
        await self._redis.delete(_session_key(session_id))

    async def begin_sign_in(self, next_path: str) -> str:
        state = secrets.token_urlsafe(24)
        await self._redis.set(SIGN_IN_PREFIX + state, next_path, ex=SIGN_IN_TTL_SECONDS)
        return state

    async def finish_sign_in(self, state: str) -> str | None:
        # GETDEL reads and deletes in one step, so a state can never be used twice.
        return cast(str | None, await self._redis.getdel(SIGN_IN_PREFIX + state))


def _session_key(session_id: str) -> str:
    return SESSION_PREFIX + hashlib.sha256(session_id.encode()).hexdigest()


def create_session_store(redis: Redis, settings: Settings) -> RedisSessionStore:
    return RedisSessionStore(redis, ttl_seconds=round(settings.session_ttl_days * 24 * 3600))
