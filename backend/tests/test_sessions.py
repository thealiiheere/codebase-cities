"""Where a sign-in may return to (no Redis needed), then the Redis session store (integration)."""

import datetime as dt
import logging

import pytest
from redis.asyncio import Redis

from app.config import Settings
from app.github.models import UserProfile
from app.services.auth import current_user, safe_next_path
from app.sessions import SESSION_PREFIX, SIGN_IN_PREFIX, RedisSessionStore, create_session_store
from tests.fakes import FakeSessionStore

USER = UserProfile(
    github_id=7,
    login="Hubot",
    name=None,
    avatar_url="https://avatars.test/7",
    created_at=dt.datetime(2012, 1, 1, tzinfo=dt.UTC),
)


@pytest.mark.parametrize(
    ("asked", "kept"),
    [
        ("/city/octocat", "/city/octocat"),
        ("/city/octocat?year=2019", "/city/octocat?year=2019"),
        ("/?demo", "/?demo"),
        # "" means: your own city.
        (None, ""),
        ("", ""),
        ("/", ""),
        # Anything that could lead to another site.
        ("//evil.test", ""),
        ("https://evil.test/", ""),
        ("/\\evil.test", ""),
        ("evil.test", ""),
        ("/city/x\r\nSet-Cookie: a=b", ""),
        ("/" + "x" * 600, ""),
    ],
)
def test_safe_next_path(asked: str | None, kept: str) -> None:
    assert safe_next_path(asked) == kept


async def test_an_unavailable_store_means_signed_out(caplog: pytest.LogCaptureFixture) -> None:
    sessions = FakeSessionStore(error=ConnectionError("redis down"))

    with caplog.at_level(logging.WARNING, logger="app.services.auth"):
        assert await current_user(sessions, "session-1") is None

    assert "Session store unavailable" in caplog.text


async def test_no_cookie_means_no_lookup() -> None:
    sessions = FakeSessionStore(error=AssertionError("should not be asked"))

    assert await current_user(sessions, None) is None
    assert await current_user(sessions, "") is None


# --- The Redis store ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_sessions_round_trip_and_end(redis_client: Redis) -> None:
    store = RedisSessionStore(redis_client, ttl_seconds=600)

    session_id = await store.create(USER)

    assert await store.get(session_id) == USER
    assert await store.get("made-up") is None
    [key] = await redis_client.keys(SESSION_PREFIX + "*")
    # Only a hash of the id is stored, so reading Redis doesn't reveal working session ids.
    assert session_id not in key
    assert 0 < await redis_client.ttl(key) <= 600

    await store.delete(session_id)
    assert await store.get(session_id) is None


@pytest.mark.integration
async def test_sessions_last_session_ttl_days(redis_client: Redis, settings: Settings) -> None:
    store = create_session_store(redis_client, settings.model_copy(update={"session_ttl_days": 2}))

    await store.create(USER)

    [key] = await redis_client.keys(SESSION_PREFIX + "*")
    assert 2 * 24 * 3600 - 60 < await redis_client.ttl(key) <= 2 * 24 * 3600


@pytest.mark.integration
async def test_a_sign_in_state_works_once(redis_client: Redis) -> None:
    store = RedisSessionStore(redis_client, ttl_seconds=600)

    state = await store.begin_sign_in("/city/octocat")

    assert 0 < await redis_client.ttl(SIGN_IN_PREFIX + state) <= 600
    assert await store.finish_sign_in(state) == "/city/octocat"
    assert await store.finish_sign_in(state) is None
    assert await store.finish_sign_in("made-up") is None
