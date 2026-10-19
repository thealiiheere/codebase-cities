"""What the mapping engine takes in: one user's GitHub data as plain, immutable values."""

import datetime as dt

from pydantic import BaseModel, ConfigDict


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class RepoData(_Frozen):
    github_id: int
    name: str
    stars: int
    forks: int
    size_kb: int
    primary_language: str | None
    # Bytes of code per language.
    languages: dict[str, int]
    created_at: dt.datetime
    pushed_at: dt.datetime | None
    # Every commit on the default branch, including any older than the fetched history.
    total_commits: int
    # Commits per UTC day. Days without commits are absent.
    daily_commits: dict[dt.date, int]
    # A private repo its owner shared; only ever in the city shown to that owner.
    private: bool = False


class UserData(_Frozen):
    login: str
    created_at: dt.datetime
    repos: tuple[RepoData, ...]
    # The user's own contributions per UTC day (their GitHub calendar). Quiet days are absent.
    contributions: dict[dt.date, int]
