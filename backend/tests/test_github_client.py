import datetime as dt

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.config import Settings
from app.github.client import GitHubClient, create_github_client
from app.github.errors import GitHubApiError, GitHubAuthError, GitHubUserNotFoundError
from app.github.transport import GitHubTransport
from tests.github_fakes import (
    API,
    EMPTY_REPO,
    GRAPHQL_URL,
    FakeHistoryServer,
    commit_dates,
    gql,
    not_found_error,
    repo_node,
    repos_page,
    request_query,
    request_variables,
)

# --- lookup_user (REST) ------------------------------------------------------------------------

REST_USER = {
    "id": 583231,
    "login": "octocat",
    "name": "The Octocat",
    "avatar_url": "https://avatars.githubusercontent.com/u/583231",
    "created_at": "2011-01-25T18:44:36Z",
    "type": "User",
}


async def test_lookup_user_returns_profile(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{API}/users/octocat").mock(return_value=httpx.Response(200, json=REST_USER))

    profile = await github.lookup_user("octocat")

    assert profile.github_id == 583231
    assert profile.login == "octocat"
    assert profile.name == "The Octocat"
    assert profile.created_at == dt.datetime(2011, 1, 25, 18, 44, 36, tzinfo=dt.UTC)


async def test_lookup_unknown_user(github: GitHubClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.get(f"{API}/users/ghost").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    with pytest.raises(GitHubUserNotFoundError, match="'ghost' does not exist"):
        await github.lookup_user("ghost")


async def test_lookup_rejects_organizations(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(f"{API}/users/github").mock(
        return_value=httpx.Response(
            200, json={**REST_USER, "login": "github", "type": "Organization"}
        )
    )

    with pytest.raises(GitHubUserNotFoundError, match="is an organization"):
        await github.lookup_user("github")


@pytest.mark.parametrize("login", ["", "-leading", "trailing-", "dou--ble", "../etc", "a" * 40])
async def test_invalid_logins_never_reach_github(github: GitHubClient, login: str) -> None:
    # No routes are mocked, so any request would fail the test.
    with pytest.raises(GitHubUserNotFoundError, match="not a valid GitHub username"):
        await github.lookup_user(login)


# --- get_repos (GraphQL) -----------------------------------------------------------------------


async def test_get_repos_maps_fields(github: GitHubClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(GRAPHQL_URL).mock(return_value=gql(repos_page([repo_node("city")])))

    [repo] = await github.get_repos("octocat", limit=100)

    assert repo.name == "city"
    assert repo.stars == 3
    assert repo.forks == 1
    assert repo.size_kb == 250
    assert repo.primary_language == "Python"
    assert repo.languages == {"Python": 9000, "Shell": 1000}
    assert repo.total_commits == 42
    assert repo.created_at == dt.datetime(2020, 1, 15, 10, 0, tzinfo=dt.UTC)
    assert repo.pushed_at == dt.datetime(2026, 10, 1, 9, 30, tzinfo=dt.UTC)


async def test_get_repos_handles_empty_and_unusual_repos(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    empty = repo_node(
        "empty",
        primaryLanguage=None,
        languages={"edges": []},
        defaultBranchRef=None,
        pushedAt=None,
        diskUsage=None,
    )
    respx_mock.post(GRAPHQL_URL).mock(return_value=gql(repos_page([empty])))

    [repo] = await github.get_repos("octocat", limit=100)

    assert repo.primary_language is None
    assert repo.languages == {}
    assert repo.total_commits == 0
    assert repo.pushed_at is None
    assert repo.size_kb == 0


async def test_get_repos_asks_only_for_public_owned_non_forks(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(GRAPHQL_URL).mock(return_value=gql(repos_page([])))

    await github.get_repos("octocat", limit=100)

    query = request_query(route.calls.last.request)
    for argument in ("privacy: PUBLIC", "isFork: false", "ownerAffiliations: [OWNER]"):
        assert argument in query
    assert "field: PUSHED_AT, direction: DESC" in query


async def test_get_repos_pages_until_the_limit(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    first_page = [repo_node(f"repo-{i}") for i in range(100)]
    second_page = [repo_node(f"repo-{i}") for i in range(100, 150)]
    route = respx_mock.post(GRAPHQL_URL).mock(
        side_effect=[
            gql(repos_page(first_page, has_next=True, cursor="after-100")),
            gql(repos_page(second_page, has_next=True, cursor="after-150")),
        ]
    )

    repos = await github.get_repos("octocat", limit=150)

    assert len(repos) == 150
    first, second = (request_variables(call.request) for call in route.calls)
    assert (first["first"], first["after"]) == (100, None)
    assert (second["first"], second["after"]) == (50, "after-100")


async def test_get_repos_unknown_user(github: GitHubClient, respx_mock: respx.MockRouter) -> None:
    respx_mock.post(GRAPHQL_URL).mock(return_value=gql({"user": None}, [not_found_error("user")]))

    with pytest.raises(GitHubUserNotFoundError):
        await github.get_repos("ghost", limit=100)


async def test_get_repos_surfaces_other_graphql_errors(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=gql(None, [{"type": "INTERNAL", "message": "Something went wrong"}])
    )

    with pytest.raises(GitHubApiError, match="Something went wrong"):
        await github.get_repos("octocat", limit=100)


# --- get_contribution_calendar -----------------------------------------------------------------


def calendar_response(request: httpx.Request) -> httpx.Response:
    """Answers the years query, then each calendar query with two days per requested year."""
    variables = request_variables(request)
    if "from0" not in variables:
        years = [2019, 2020, 2021, 2022, 2023, 2024, 2025]
        return gql({"user": {"contributionsCollection": {"contributionYears": years}}})
    user = {}
    i = 0
    while f"from{i}" in variables:
        year = variables[f"from{i}"][:4]
        days = [
            {"date": f"{year}-03-01", "contributionCount": 2},
            {"date": f"{year}-03-02", "contributionCount": 0},
        ]
        user[f"y{i}"] = {"contributionCalendar": {"weeks": [{"contributionDays": days}]}}
        i += 1
    return gql({"user": user})


async def test_contribution_calendar_covers_every_year_in_batches(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(GRAPHQL_URL).mock(side_effect=calendar_response)

    days = await github.get_contribution_calendar("octocat")

    assert len(days) == 14  # 7 years x 2 days, in date order.
    assert days[0].date == dt.date(2019, 3, 1)
    assert days[0].count == 2
    assert days[-1].date == dt.date(2025, 3, 2)
    assert days == sorted(days, key=lambda day: day.date)
    # One query for the years, then 7 years in chunks of 5 and 2.
    assert route.call_count == 3
    second_chunk = request_variables(route.calls[2].request)
    assert second_chunk["from0"] == "2024-01-01T00:00:00Z"
    assert second_chunk["to1"] == "2025-12-31T23:59:59Z"


async def test_contribution_calendar_unknown_user(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(GRAPHQL_URL).mock(return_value=gql({"user": None}, [not_found_error("user")]))

    with pytest.raises(GitHubUserNotFoundError):
        await github.get_contribution_calendar("ghost")


# --- get_commit_history ------------------------------------------------------------------------


async def test_commit_history_follows_pages(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    server = FakeHistoryServer({"big": commit_dates(250), "small": commit_dates(3)})
    respx_mock.post(GRAPHQL_URL).mock(side_effect=server)

    histories = await github.get_commit_history("octocat", ["big", "small"])

    assert len(histories["big"].committed_at) == 250
    assert len(histories["small"].committed_at) == 3
    assert histories["big"].complete and histories["small"].complete
    # Both repos share the first query; only "big" needs pages 2 and 3.
    assert [sorted(k for k in r if k.startswith("name")) for r in server.requests] == [
        ["name0", "name1"],
        ["name0"],
        ["name0"],
    ]


async def test_commit_history_batches_repos_into_shared_queries(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    names = [f"repo-{i}" for i in range(12)]
    server = FakeHistoryServer({name: commit_dates(5) for name in names})
    respx_mock.post(GRAPHQL_URL).mock(side_effect=server)

    histories = await github.get_commit_history("octocat", names)

    assert set(histories) == set(names)
    batch_sizes = [
        sum(1 for key in request if key.startswith("name")) for request in server.requests
    ]
    assert batch_sizes == [10, 2]


async def test_commit_history_stops_at_the_cap(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter
) -> None:
    github = GitHubClient(github_transport, max_commits_per_repo=150)
    server = FakeHistoryServer({"huge": commit_dates(1000)})
    respx_mock.post(GRAPHQL_URL).mock(side_effect=server)

    history = (await github.get_commit_history("octocat", ["huge"]))["huge"]

    assert len(history.committed_at) == 150
    assert not history.complete
    assert len(server.requests) == 2  # Stopped after reaching the cap, not all 10 pages.


async def test_commit_history_exactly_at_the_cap_is_complete(
    github_transport: GitHubTransport, respx_mock: respx.MockRouter
) -> None:
    github = GitHubClient(github_transport, max_commits_per_repo=200)
    respx_mock.post(GRAPHQL_URL).mock(side_effect=FakeHistoryServer({"r": commit_dates(200)}))

    history = (await github.get_commit_history("octocat", ["r"]))["r"]

    assert len(history.committed_at) == 200
    assert history.complete


async def test_commit_history_newest_first_as_datetimes(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(GRAPHQL_URL).mock(side_effect=FakeHistoryServer({"r": commit_dates(2)}))

    history = (await github.get_commit_history("octocat", ["r"]))["r"]

    newest, older = history.committed_at
    assert newest > older
    assert newest.tzinfo is not None


async def test_commit_history_tolerates_empty_and_missing_repos(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    server = FakeHistoryServer({"empty": EMPTY_REPO, "ok": commit_dates(1)})
    respx_mock.post(GRAPHQL_URL).mock(side_effect=server)

    histories = await github.get_commit_history("octocat", ["empty", "deleted", "ok"])

    assert histories["empty"].committed_at == ()
    assert histories["deleted"].committed_at == ()
    assert len(histories["ok"].committed_at) == 1


async def test_commit_history_passes_since(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    server = FakeHistoryServer({"r": commit_dates(1)})
    respx_mock.post(GRAPHQL_URL).mock(side_effect=server)
    since = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)

    await github.get_commit_history("octocat", ["r"], since=since)

    assert server.requests[0]["since"] == "2026-09-01T00:00:00+00:00"
    assert server.requests[0]["owner"] == "octocat"


async def test_commit_history_surfaces_unexpected_errors(
    github: GitHubClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(GRAPHQL_URL).mock(
        return_value=gql(None, [{"type": "INTERNAL", "message": "Timeout"}])
    )

    with pytest.raises(GitHubApiError, match="Timeout"):
        await github.get_commit_history("octocat", ["r"])


# --- factory -----------------------------------------------------------------------------------


async def test_factory_requires_a_token(settings: Settings) -> None:
    with pytest.raises(GitHubAuthError):
        create_github_client(settings)


async def test_factory_applies_settings(respx_mock: respx.MockRouter) -> None:
    settings = Settings(
        _env_file=None, github_token=SecretStr("ghp_example"), github_max_commits_per_repo=300
    )
    route = respx_mock.post(GRAPHQL_URL).mock(
        side_effect=FakeHistoryServer({"r": commit_dates(1000)})
    )

    async with create_github_client(settings) as github:
        history = (await github.get_commit_history("octocat", ["r"]))["r"]

    assert len(history.committed_at) == 300
    assert route.calls.last.request.headers["authorization"] == "Bearer ghp_example"
