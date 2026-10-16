"""In-memory stand-ins for the GitHub client and the job queue, plus data builders."""

import datetime as dt
import uuid
from collections.abc import Awaitable, Callable

from app.github.errors import GitHubUserNotFoundError
from app.github.models import CommitHistory, ContributionDay, RepoInfo, UserProfile

T0 = dt.datetime(2026, 10, 16, 12, 0, tzinfo=dt.UTC)


def make_profile(login: str = "Octocat", github_id: int = 583231) -> UserProfile:
    return UserProfile(
        github_id=github_id,
        login=login,
        name="The Octocat",
        avatar_url=f"https://avatars.example/{github_id}",
        created_at=dt.datetime(2011, 1, 25, tzinfo=dt.UTC),
    )


def make_repo(name: str, github_id: int, **overrides: object) -> RepoInfo:
    fields: dict[str, object] = {
        "github_id": github_id,
        "name": name,
        "stars": 10,
        "forks": 2,
        "size_kb": 300,
        "primary_language": "Python",
        "languages": {"Python": 9000, "Shell": 1000},
        "created_at": dt.datetime(2020, 1, 1, tzinfo=dt.UTC),
        "pushed_at": T0,
        "total_commits": 3,
    }
    fields.update(overrides)
    return RepoInfo.model_validate(fields)


def make_history(name: str, *timestamps: dt.datetime, complete: bool = True) -> CommitHistory:
    return CommitHistory(repo_name=name, committed_at=timestamps, complete=complete)


class FakeGitHub:
    """Serves one user's data. `error` (if set) is raised by every method; `on_call` is awaited
    first, letting tests look at state mid-job."""

    def __init__(
        self,
        profile: UserProfile | None = None,
        repos: list[RepoInfo] | None = None,
        calendar: list[ContributionDay] | None = None,
        histories: dict[str, CommitHistory] | None = None,
        error: BaseException | None = None,
        on_call: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        self.profile = profile
        self.repos = repos or []
        self.calendar = calendar or []
        self.histories = histories or {}
        self.error = error
        self.on_call = on_call
        self.calls: list[str] = []

    async def _record(self, method: str) -> None:
        self.calls.append(method)
        if self.on_call:
            await self.on_call(method)
        if self.error:
            raise self.error

    async def lookup_user(self, login: str) -> UserProfile:
        await self._record("lookup_user")
        if self.profile is None or self.profile.login.lower() != login.lower():
            raise GitHubUserNotFoundError(login)
        return self.profile

    async def get_repos(self, login: str, limit: int) -> list[RepoInfo]:
        await self._record("get_repos")
        return self.repos[:limit]

    async def get_contribution_calendar(self, login: str) -> list[ContributionDay]:
        await self._record("get_contribution_calendar")
        return self.calendar

    async def get_commit_history(
        self, owner: str, repo_names: list[str], *, since: dt.datetime | None = None
    ) -> dict[str, CommitHistory]:
        await self._record("get_commit_history")
        empty = CommitHistory(repo_name="", committed_at=(), complete=True)
        return {name: self.histories.get(name, empty) for name in repo_names}


class FakeQueue:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.enqueued: list[uuid.UUID] = []

    async def enqueue_ingestion(self, job_id: uuid.UUID) -> None:
        if self.error:
            raise self.error
        self.enqueued.append(job_id)


class FakeCityCache:
    """Dict-backed city cache that counts hits and misses. `error` makes every call fail."""

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.entries: dict[str, str] = {}
        self.hits = 0
        self.misses = 0

    async def get(self, key: str) -> str | None:
        if self.error:
            raise self.error
        value = self.entries.get(key)
        if value is None:
            self.misses += 1
        else:
            self.hits += 1
        return value

    async def set(self, key: str, value: str) -> None:
        if self.error:
            raise self.error
        self.entries[key] = value
