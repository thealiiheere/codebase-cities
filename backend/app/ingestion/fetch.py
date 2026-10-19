"""Step 1: everything we need about one user, straight from GitHub."""

import datetime as dt
from dataclasses import dataclass
from typing import Protocol

from app.github.models import CommitHistory, ContributionDay, RepoInfo, UserProfile


class GitHubSource(Protocol):
    """The GitHub client methods ingestion uses. Tests substitute a fake with the same shape."""

    async def lookup_user(self, login: str) -> UserProfile: ...

    async def get_repos(self, login: str, limit: int) -> list[RepoInfo]: ...

    async def get_contribution_calendar(self, login: str) -> list[ContributionDay]: ...

    async def get_commit_history(
        self, owner: str, repo_names: list[str], *, since: dt.datetime | None = None
    ) -> dict[str, CommitHistory]: ...


class PrivateGitHubSource(Protocol):
    """What fetching someone's private repos needs, from a client using *their* token."""

    async def get_own_private_repos(self, limit: int) -> tuple[str, list[RepoInfo]]: ...

    async def get_commit_history(
        self, owner: str, repo_names: list[str], *, since: dt.datetime | None = None
    ) -> dict[str, CommitHistory]: ...


@dataclass(frozen=True)
class FetchedUserData:
    profile: UserProfile
    repos: list[RepoInfo]
    calendar: list[ContributionDay]
    # Keyed by repo name.
    histories: dict[str, CommitHistory]


async def fetch_user_data(
    github: GitHubSource, username: str, *, max_repos: int
) -> FetchedUserData:
    # The profile comes first: it raises a clear error for unknown users before we spend quota,
    # and gives the login's canonical capitalization for the later calls.
    profile = await github.lookup_user(username)
    repos = await github.get_repos(profile.login, limit=max_repos)
    calendar = await github.get_contribution_calendar(profile.login)
    histories = await github.get_commit_history(profile.login, [repo.name for repo in repos])
    return FetchedUserData(profile=profile, repos=repos, calendar=calendar, histories=histories)


@dataclass(frozen=True)
class FetchedPrivateRepos:
    # The token owner's login, as GitHub capitalizes it.
    login: str
    repos: list[RepoInfo]
    histories: dict[str, CommitHistory]


async def fetch_private_repos(
    github: PrivateGitHubSource, *, max_repos: int
) -> FetchedPrivateRepos:
    login, repos = await github.get_own_private_repos(limit=max_repos)
    histories = (
        await github.get_commit_history(login, [repo.name for repo in repos]) if repos else {}
    )
    return FetchedPrivateRepos(login=login, repos=repos, histories=histories)
