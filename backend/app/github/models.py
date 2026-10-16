"""What the GitHub client returns: plain, validated data with no GitHub response shapes."""

import datetime as dt

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class UserProfile(_Frozen):
    github_id: int
    login: str
    name: str | None
    avatar_url: str
    created_at: dt.datetime


class RepoInfo(_Frozen):
    github_id: int
    name: str
    stars: int
    forks: int
    size_kb: int
    primary_language: str | None
    # Bytes of code per language, largest first (top 20 only).
    languages: dict[str, int]
    created_at: dt.datetime
    pushed_at: dt.datetime | None
    # All commits on the default branch, even when the commit history was capped.
    total_commits: int


class ContributionDay(_Frozen):
    date: dt.date
    count: int


class CommitHistory(_Frozen):
    repo_name: str
    # Commit timestamps on the default branch, newest first.
    committed_at: tuple[dt.datetime, ...]
    # False when the history was cut off at the per-repo cap, so older commits are missing.
    complete: bool
