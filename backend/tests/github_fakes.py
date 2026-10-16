"""Builders and fakes for GitHub client tests. Nothing here talks to the real GitHub."""

import datetime as dt
import json
import zlib
from typing import Any

import httpx

API = "https://api.github.com"
GRAPHQL_URL = f"{API}/graphql"
NOW = dt.datetime(2026, 10, 16, 12, 0, tzinfo=dt.UTC)


class FakeTime:
    """A clock that only moves when the code under test sleeps, so waits are instant."""

    def __init__(self, start: dt.datetime = NOW) -> None:
        self.current = start
        self.sleeps: list[float] = []

    def now(self) -> dt.datetime:
        return self.current

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.current += dt.timedelta(seconds=seconds)


def rate_headers(
    *, remaining: int, reset_in: float, resource: str = "graphql", now: dt.datetime = NOW
) -> dict[str, str]:
    reset_epoch = int((now + dt.timedelta(seconds=reset_in)).timestamp())
    return {
        "x-ratelimit-limit": "5000",
        "x-ratelimit-remaining": str(remaining),
        "x-ratelimit-reset": str(reset_epoch),
        "x-ratelimit-resource": resource,
    }


def gql(
    data: dict[str, Any] | None,
    errors: list[dict[str, Any]] | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"data": data}
    if errors:
        body["errors"] = errors
    return httpx.Response(200, json=body, headers=headers)


def not_found_error(path: str) -> dict[str, Any]:
    return {"type": "NOT_FOUND", "path": [path], "message": f"Could not resolve {path}."}


def request_variables(request: httpx.Request) -> dict[str, Any]:
    variables: dict[str, Any] = json.loads(request.content)["variables"]
    return variables


def request_query(request: httpx.Request) -> str:
    query: str = json.loads(request.content)["query"]
    return query


def repo_node(name: str, **overrides: Any) -> dict[str, Any]:
    node: dict[str, Any] = {
        "databaseId": zlib.crc32(name.encode()),
        "name": name,
        "stargazerCount": 3,
        "forkCount": 1,
        "diskUsage": 250,
        "createdAt": "2020-01-15T10:00:00Z",
        "pushedAt": "2026-10-01T09:30:00Z",
        "primaryLanguage": {"name": "Python"},
        "languages": {
            "edges": [
                {"size": 9000, "node": {"name": "Python"}},
                {"size": 1000, "node": {"name": "Shell"}},
            ]
        },
        "defaultBranchRef": {"target": {"history": {"totalCount": 42}}},
    }
    node.update(overrides)
    return node


def repos_page(nodes: list[dict[str, Any]], *, has_next: bool = False, cursor: str = "c1") -> Any:
    return {
        "user": {
            "repositories": {
                "pageInfo": {"hasNextPage": has_next, "endCursor": cursor if has_next else None},
                "nodes": nodes,
            }
        }
    }


EMPTY_REPO = "__empty__"


class FakeHistoryServer:
    """Serves batched commit-history queries from in-memory repos, 100 commits per page.

    `repos` maps repo name to its commit timestamps (newest first), or to EMPTY_REPO for a repo
    with no commits. Repos not in the map get a NOT_FOUND error, like a deleted repo would.
    """

    def __init__(self, repos: dict[str, list[str] | str]) -> None:
        self.repos = repos
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        variables = request_variables(request)
        self.requests.append(variables)
        data: dict[str, Any] = {}
        errors = []
        i = 0
        while f"name{i}" in variables:
            alias, name = f"r{i}", variables[f"name{i}"]
            commits = self.repos.get(name)
            if commits is None:
                data[alias] = None
                errors.append(not_found_error(alias))
            elif commits == EMPTY_REPO:
                data[alias] = {"defaultBranchRef": None}
            else:
                offset = int(variables[f"cursor{i}"] or 0)
                page = commits[offset : offset + 100]
                has_next = offset + 100 < len(commits)
                data[alias] = {
                    "defaultBranchRef": {
                        "target": {
                            "history": {
                                "pageInfo": {
                                    "hasNextPage": has_next,
                                    "endCursor": str(offset + 100),
                                },
                                "nodes": [{"committedDate": date} for date in page],
                            }
                        }
                    }
                }
            i += 1
        return gql(data, errors)


def commit_dates(count: int, *, newest: dt.datetime = NOW) -> list[str]:
    """`count` ISO timestamps, one per hour going back from `newest`."""
    return [
        (newest - dt.timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M:%SZ") for hour in range(count)
    ]
