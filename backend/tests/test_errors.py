import httpx
import pytest
from fastapi import FastAPI

from app.errors import NotFoundError, ServiceUnavailableError


@pytest.fixture
def app_with_test_routes(app: FastAPI) -> FastAPI:
    @app.get("/boom/not-found")
    async def raise_not_found() -> None:
        raise NotFoundError("No such thing.")

    @app.get("/boom/unavailable")
    async def raise_unavailable() -> None:
        raise ServiceUnavailableError("Try later.")

    @app.get("/boom/crash")
    async def crash() -> None:
        raise RuntimeError("secret internal detail")

    @app.get("/boom/needs-int")
    async def needs_int(count: int) -> int:
        return count

    return app


@pytest.fixture
async def test_client(app_with_test_routes: FastAPI) -> httpx.AsyncClient:
    # raise_app_exceptions=False: Starlette re-raises unhandled errors after responding, and
    # we want to inspect the 500 response the client would actually receive.
    transport = httpx.ASGITransport(app=app_with_test_routes, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.parametrize(
    ("path", "status", "code", "message"),
    [
        ("/boom/not-found", 404, "not_found", "No such thing."),
        ("/boom/unavailable", 503, "service_unavailable", "Try later."),
        ("/no/such/route", 404, "not_found", "Not Found"),
        ("/boom/crash", 500, "internal_error", "An unexpected error occurred."),
        ("/boom/needs-int?count=abc", 422, "validation_error", None),
    ],
)
async def test_errors_use_consistent_shape(
    test_client: httpx.AsyncClient, path: str, status: int, code: str, message: str | None
) -> None:
    async with test_client:
        response = await test_client.get(path)

    assert response.status_code == status
    body = response.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    if message is not None:
        assert body["error"]["message"] == message


async def test_validation_error_message_names_the_field(test_client: httpx.AsyncClient) -> None:
    async with test_client:
        response = await test_client.get("/boom/needs-int?count=abc")

    assert response.json()["error"]["message"].startswith("query.count:")


async def test_wrong_method_is_reported(test_client: httpx.AsyncClient) -> None:
    async with test_client:
        response = await test_client.post("/api/health")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"


async def test_unexpected_errors_do_not_leak_details(test_client: httpx.AsyncClient) -> None:
    async with test_client:
        response = await test_client.get("/boom/crash")

    assert "secret" not in response.text
