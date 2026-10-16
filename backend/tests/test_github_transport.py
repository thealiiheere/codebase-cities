import httpx
import pytest
import respx

from app.github.errors import (
    GitHubApiError,
    GitHubAuthError,
    GitHubNotFoundError,
    RateLimitExceededError,
)
from app.github.transport import GitHubTransport
from tests.github_fakes import API, GRAPHQL_URL, NOW, FakeTime, gql, rate_headers


async def test_sends_auth_and_api_headers(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(GRAPHQL_URL).mock(return_value=gql({"ok": True}))

    await github_transport.graphql("query { ok }", {})

    headers = route.calls.last.request.headers
    assert headers["authorization"] == "Bearer test-token"
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["x-github-api-version"] == "2022-11-28"
    assert headers["user-agent"] == "codebase-cities"


def test_refuses_to_start_without_a_token() -> None:
    with pytest.raises(GitHubAuthError, match="GITHUB_TOKEN is not set"):
        GitHubTransport("")


async def test_graphql_returns_data_and_errors(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=gql({"user": None}, [{"type": "NOT_FOUND", "path": ["user"], "message": "x"}])
    )

    result = await github_transport.graphql("query { user }", {"login": "nobody"})

    assert result.data == {"user": None}
    assert [(e.type, e.path) for e in result.errors] == [("NOT_FOUND", ["user"])]


async def test_retries_server_errors_with_backoff(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter, fake_time: FakeTime
) -> None:
    route = respx_mock.post(GRAPHQL_URL).mock(
        side_effect=[httpx.Response(502), httpx.Response(503), gql({"ok": True})]
    )

    result = await github_transport.graphql("query { ok }", {})

    assert result.data == {"ok": True}
    assert route.call_count == 3
    # Backoff doubles (1s, then 2s) plus up to 1s of jitter.
    assert len(fake_time.sleeps) == 2
    assert 1 <= fake_time.sleeps[0] < 2
    assert 2 <= fake_time.sleeps[1] < 3


async def test_gives_up_after_max_retries(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(GRAPHQL_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(GitHubApiError) as caught:
        await github_transport.graphql("query { ok }", {})

    assert caught.value.status_code == 503
    assert route.call_count == 4  # The first try plus 3 retries.


async def test_retries_network_errors(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(GRAPHQL_URL).mock(
        side_effect=[httpx.ConnectError("connection refused"), gql({"ok": True})]
    )

    result = await github_transport.graphql("query { ok }", {})

    assert result.data == {"ok": True}


async def test_reports_unreachable_github_after_retries(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(GRAPHQL_URL).mock(side_effect=httpx.ConnectTimeout("timed out"))

    with pytest.raises(GitHubApiError, match="Could not reach GitHub"):
        await github_transport.graphql("query { ok }", {})


async def test_waits_out_a_secondary_rate_limit(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter, fake_time: FakeTime
) -> None:
    limited = httpx.Response(403, headers={"retry-after": "30"}, json={"message": "slow down"})
    respx_mock.post(GRAPHQL_URL).mock(side_effect=[limited, gql({"ok": True})])

    result = await github_transport.graphql("query { ok }", {})

    assert result.data == {"ok": True}
    assert fake_time.sleeps == [30]


async def test_waits_for_a_close_primary_reset(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter, fake_time: FakeTime
) -> None:
    exhausted = httpx.Response(403, headers=rate_headers(remaining=0, reset_in=20))
    respx_mock.post(GRAPHQL_URL).mock(side_effect=[exhausted, gql({"ok": True})])

    await github_transport.graphql("query { ok }", {})

    assert fake_time.sleeps == [20]


async def test_fails_fast_when_the_reset_is_far_away(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter, fake_time: FakeTime
) -> None:
    exhausted = httpx.Response(429, headers=rate_headers(remaining=0, reset_in=1800))
    respx_mock.post(GRAPHQL_URL).mock(return_value=exhausted)

    with pytest.raises(RateLimitExceededError) as caught:
        await github_transport.graphql("query { ok }", {})

    assert caught.value.resource == "graphql"
    assert caught.value.reset_at is not None
    assert int(caught.value.reset_at.timestamp()) == int(NOW.timestamp()) + 1800
    assert fake_time.sleeps == []


async def test_backs_off_before_draining_the_budget(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter, fake_time: FakeTime
) -> None:
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=gql({"ok": True}, headers=rate_headers(remaining=40, reset_in=25))
    )

    await github_transport.graphql("query { ok }", {})  # Learns that only 40 points are left.
    await github_transport.graphql("query { ok }", {})

    assert fake_time.sleeps == [25]


async def test_stops_before_sending_when_budget_is_gone(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(GRAPHQL_URL).mock(
        return_value=gql({"ok": True}, headers=rate_headers(remaining=5, reset_in=3600))
    )
    await github_transport.graphql("query { ok }", {})

    with pytest.raises(RateLimitExceededError):
        await github_transport.graphql("query { ok }", {})

    assert route.call_count == 1


async def test_graphql_rate_limited_error_is_raised(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=gql(None, [{"type": "RATE_LIMITED", "message": "API rate limit exceeded"}])
    )

    with pytest.raises(RateLimitExceededError):
        await github_transport.graphql("query { ok }", {})


async def test_plain_forbidden_is_not_retried(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(f"{API}/users/someone").mock(
        return_value=httpx.Response(403, json={"message": "Resource not accessible"})
    )

    with pytest.raises(GitHubApiError, match="Resource not accessible") as caught:
        await github_transport.rest_get("/users/someone")

    assert caught.value.status_code == 403
    assert route.call_count == 1


async def test_bad_token_is_reported_clearly(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(GRAPHQL_URL).mock(return_value=httpx.Response(401))

    with pytest.raises(GitHubAuthError, match="invalid or expired"):
        await github_transport.graphql("query { ok }", {})


async def test_rest_404_raises_not_found(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{API}/users/ghost").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    with pytest.raises(GitHubNotFoundError):
        await github_transport.rest_get("/users/ghost")


async def test_rest_reuses_etag_and_serves_cached_body_on_304(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(f"{API}/users/octocat").mock(
        side_effect=[
            httpx.Response(200, json={"login": "octocat"}, headers={"etag": 'W/"abc"'}),
            httpx.Response(304),
        ]
    )

    first = await github_transport.rest_get("/users/octocat")
    second = await github_transport.rest_get("/users/octocat")

    assert first == second == {"login": "octocat"}
    assert "if-none-match" not in route.calls[0].request.headers
    assert route.calls[1].request.headers["if-none-match"] == 'W/"abc"'


async def test_etags_are_kept_per_query_string(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.get(f"{API}/users/octocat/repos").mock(
        return_value=httpx.Response(200, json=[], headers={"etag": '"page"'})
    )

    await github_transport.rest_get("/users/octocat/repos", {"page": 1})
    await github_transport.rest_get("/users/octocat/repos", {"page": 2})

    assert "if-none-match" not in route.calls[1].request.headers
