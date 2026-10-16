import asyncio

import httpx
import pytest
from fastapi import FastAPI

from app.api.deps import get_redis, get_session
from app.services import health as health_service


class FakeSession:
    def __init__(self, error: Exception | None = None, delay: float = 0) -> None:
        self.error = error
        self.delay = delay

    async def execute(self, _statement: object) -> None:
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error


class FakeRedis:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def ping(self) -> bool:
        if self.error:
            raise self.error
        return True


def use_fakes(app: FastAPI, session: FakeSession, redis: FakeRedis) -> None:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_redis] = lambda: redis


async def test_health_ok_when_dependencies_respond(app: FastAPI, client: httpx.AsyncClient) -> None:
    use_fakes(app, FakeSession(), FakeRedis())

    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "redis": "ok"}


@pytest.mark.parametrize(
    ("session", "redis", "expected_message"),
    [
        (FakeSession(error=OSError("refused")), FakeRedis(), "Unavailable: database"),
        (FakeSession(), FakeRedis(error=ConnectionError("refused")), "Unavailable: redis"),
        (
            FakeSession(error=OSError("refused")),
            FakeRedis(error=ConnectionError("refused")),
            "Unavailable: database, redis",
        ),
    ],
)
async def test_health_503_names_failing_dependencies(
    app: FastAPI,
    client: httpx.AsyncClient,
    session: FakeSession,
    redis: FakeRedis,
    expected_message: str,
) -> None:
    use_fakes(app, session, redis)

    response = await client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {
        "error": {"code": "service_unavailable", "message": expected_message}
    }


async def test_health_treats_slow_dependency_as_down(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(health_service, "CHECK_TIMEOUT_SECONDS", 0.05)
    use_fakes(app, FakeSession(delay=1), FakeRedis())

    response = await client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Unavailable: database"


async def test_health_with_real_clients_reports_unreachable_services(
    app: FastAPI, client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No fakes: exercises the real lifespan, dependencies, engine, and Redis client, all
    # pointed at a closed port by the `settings` fixture.
    monkeypatch.setattr(health_service, "CHECK_TIMEOUT_SECONDS", 0.5)

    async with app.router.lifespan_context(app):
        response = await client.get("/api/health")

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Unavailable: database, redis"
