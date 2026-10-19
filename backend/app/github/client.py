"""High-level GitHub client: the data Codebase Cities needs, as plain models.

Usage:
    async with create_github_client(settings) as github:
        profile = await github.lookup_user("octocat")
"""

import datetime as dt
import itertools
from typing import Any, Self, cast

from app.config import Settings
from app.github.errors import (
    GitHubApiError,
    GitHubNotFoundError,
    GitHubUserNotFoundError,
)
from app.github.etag import EtagStore
from app.github.models import CommitHistory, ContributionDay, RepoInfo, UserProfile
from app.github.payloads import (
    ContributionsCollection,
    HistoryPage,
    RepoHistory,
    RestUser,
    UserContributionYears,
    UserRepos,
)
from app.github.queries import (
    CONTRIBUTION_YEARS_QUERY,
    MAX_PAGE_SIZE,
    REPOS_QUERY,
    VIEWER_PRIVATE_REPOS_QUERY,
    build_calendar_query,
    build_history_query,
)
from app.github.ratelimit import RateLimitState
from app.github.transport import GitHubTransport, GraphQLResult
from app.usernames import is_valid_login

# Repos per batched history query. Large enough to save quota, small enough to stay well under
# GitHub's 10-second query timeout.
HISTORY_BATCH_SIZE = 10
# Years per calendar query, for the same reason.
CALENDAR_YEARS_PER_QUERY = 5


class GitHubClient:
    def __init__(self, transport: GitHubTransport, *, max_commits_per_repo: int = 5000) -> None:
        self._transport = transport
        self._max_commits_per_repo = max_commits_per_repo

    @property
    def rate_limits(self) -> dict[str, RateLimitState]:
        return self._transport.rate_limits

    async def lookup_user(self, login: str) -> UserProfile:
        """Profile via REST, so repeat lookups are free 304s. Rejects organizations."""
        _validate_login(login)
        try:
            body = await self._transport.rest_get(f"/users/{login}")
        except GitHubNotFoundError:
            raise GitHubUserNotFoundError(login) from None
        user = RestUser.model_validate(body)
        if user.type != "User":
            raise GitHubUserNotFoundError(login, "is an organization, not a user")
        return user.to_profile()

    async def get_repos(self, login: str, limit: int) -> list[RepoInfo]:
        """Public, non-fork repos the user owns, most recently pushed first, at most `limit`."""
        _validate_login(login)
        repos: list[RepoInfo] = []
        cursor: str | None = None
        while len(repos) < limit:
            variables = {
                "login": login,
                "first": min(limit - len(repos), MAX_PAGE_SIZE),
                "after": cursor,
            }
            result = await self._transport.graphql(REPOS_QUERY, variables)
            connection = UserRepos.model_validate(_user_data(result, login)).repositories
            repos.extend(node.to_repo_info() for node in connection.nodes)
            if not connection.page_info.has_next_page:
                break
            cursor = connection.page_info.end_cursor
        return repos[:limit]

    async def get_own_private_repos(self, limit: int) -> tuple[str, list[RepoInfo]]:
        """The token owner's login and their private, non-fork repos (those the token may read),
        most recently pushed first, at most `limit`."""
        repos: list[RepoInfo] = []
        login = ""
        cursor: str | None = None
        while len(repos) < limit:
            variables = {"first": min(limit - len(repos), MAX_PAGE_SIZE), "after": cursor}
            result = await self._transport.graphql(VIEWER_PRIVATE_REPOS_QUERY, variables)
            if result.errors:
                raise GitHubApiError(f"GitHub GraphQL error: {result.errors[0].message}")
            viewer = cast(dict[str, Any], result.data.get("viewer") or {})
            login = str(viewer.get("login", login))
            connection = UserRepos.model_validate(viewer).repositories
            repos.extend(node.to_repo_info() for node in connection.nodes)
            if not connection.page_info.has_next_page:
                break
            cursor = connection.page_info.end_cursor
        return login, repos[:limit]

    async def get_contribution_calendar(self, login: str) -> list[ContributionDay]:
        """Every day of the contributions calendar since the account's first active year."""
        _validate_login(login)
        result = await self._transport.graphql(CONTRIBUTION_YEARS_QUERY, {"login": login})
        user = UserContributionYears.model_validate(_user_data(result, login))
        years = sorted(user.contributions_collection.contribution_years)

        counts: dict[dt.date, int] = {}
        for chunk in itertools.batched(years, CALENDAR_YEARS_PER_QUERY):
            variables: dict[str, Any] = {"login": login}
            for i, year in enumerate(chunk):
                variables[f"from{i}"] = f"{year}-01-01T00:00:00Z"
                variables[f"to{i}"] = f"{year}-12-31T23:59:59Z"
            result = await self._transport.graphql(build_calendar_query(len(chunk)), variables)
            data = _user_data(result, login)
            for i in range(len(chunk)):
                collection = ContributionsCollection.model_validate(data[f"y{i}"])
                for week in collection.contribution_calendar.weeks:
                    for day in week.contribution_days:
                        counts[day.date] = day.contribution_count

        return [ContributionDay(date=date, count=count) for date, count in sorted(counts.items())]

    async def get_commit_history(
        self, owner: str, repo_names: list[str], *, since: dt.datetime | None = None
    ) -> dict[str, CommitHistory]:
        """Default-branch commit dates per repo, newest first, capped per repo.

        Pass `since` to fetch only commits after a point in time (for incremental refreshes).
        A repo that no longer exists comes back empty rather than failing the whole call.
        """
        _validate_login(owner)
        progress = {name: _HistoryProgress(name, self._max_commits_per_repo) for name in repo_names}
        pending = list(repo_names)
        while pending:
            batch = pending[:HISTORY_BATCH_SIZE]
            await self._fetch_history_page(owner, batch, progress, since)
            pending = [name for name in pending if not progress[name].done]
        return {name: progress[name].to_commit_history() for name in repo_names}

    async def _fetch_history_page(
        self,
        owner: str,
        batch: list[str],
        progress: dict[str, "_HistoryProgress"],
        since: dt.datetime | None,
    ) -> None:
        variables: dict[str, Any] = {"owner": owner, "since": since.isoformat() if since else None}
        for i, name in enumerate(batch):
            variables[f"name{i}"] = name
            variables[f"cursor{i}"] = progress[name].cursor

        result = await self._transport.graphql(build_history_query(len(batch)), variables)
        unexpected = [e for e in result.errors if not (e.type == "NOT_FOUND" and e.path)]
        if unexpected:
            raise GitHubApiError(f"GitHub GraphQL error: {unexpected[0].message}")

        for i, name in enumerate(batch):
            repo = result.data.get(f"r{i}")
            page = RepoHistory.model_validate(repo).page if repo is not None else None
            progress[name].add_page(page)

    async def aclose(self) -> None:
        await self._transport.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


class _HistoryProgress:
    """Commit dates gathered so far for one repo, across pages."""

    def __init__(self, repo_name: str, cap: int) -> None:
        self.repo_name = repo_name
        self.cap = cap
        self.dates: list[dt.datetime] = []
        self.cursor: str | None = None
        self.done = False
        self.complete = True

    def add_page(self, page: HistoryPage | None) -> None:
        if page is None:  # Empty repo, or the repo is gone: nothing (more) to fetch.
            self.done = True
            return
        self.dates.extend(node.committed_date for node in page.nodes)
        has_more = page.page_info.has_next_page
        if len(self.dates) > self.cap or (len(self.dates) == self.cap and has_more):
            del self.dates[self.cap :]
            self.complete = False
            self.done = True
            return
        self.cursor = page.page_info.end_cursor
        self.done = not has_more

    def to_commit_history(self) -> CommitHistory:
        return CommitHistory(
            repo_name=self.repo_name, committed_at=tuple(self.dates), complete=self.complete
        )


def _validate_login(login: str) -> None:
    # Checking before any request also keeps odd input (like "../") out of REST paths.
    if not is_valid_login(login):
        raise GitHubUserNotFoundError(login, "is not a valid GitHub username")


def _user_data(result: GraphQLResult, login: str) -> dict[str, Any]:
    """The `user` object of a GraphQL result, or the right error if there isn't one."""
    for error in result.errors:
        if error.type == "NOT_FOUND" and error.path[:1] == ["user"]:
            raise GitHubUserNotFoundError(login)
    if result.errors:
        raise GitHubApiError(f"GitHub GraphQL error: {result.errors[0].message}")
    user = result.data.get("user")
    if user is None:
        raise GitHubUserNotFoundError(login)
    return cast(dict[str, Any], user)


def create_github_client(
    settings: Settings, etag_store: EtagStore | None = None, *, token: str | None = None
) -> GitHubClient:
    """A client using the server's GITHUB_TOKEN, or `token` (a user's own) when given.

    A user's client must not share the ETag cache: its responses can include private data.
    """
    if token is None:
        token = settings.github_token.get_secret_value() if settings.github_token else ""
    transport = GitHubTransport(
        token,
        base_url=settings.github_api_url,
        etag_store=etag_store,
        max_retries=settings.github_max_retries,
        rate_limit_reserve=settings.github_rate_limit_reserve,
        max_wait_seconds=settings.github_max_wait_seconds,
        timeout_seconds=settings.github_timeout_seconds,
    )
    return GitHubClient(transport, max_commits_per_repo=settings.github_max_commits_per_repo)
