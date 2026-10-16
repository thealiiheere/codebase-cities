"""Step 2: pure functions turning fetched GitHub data into database rows. No I/O here."""

import datetime as dt
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from app.github.models import CommitHistory, ContributionDay, RepoInfo, UserProfile
from app.usernames import normalize_login

Row = dict[str, Any]


def daily_commit_counts(timestamps: Iterable[dt.datetime]) -> dict[dt.date, int]:
    """Commits per calendar day, using UTC days so the result never depends on a server's clock."""
    return dict(Counter(timestamp.astimezone(dt.UTC).date() for timestamp in timestamps))


def user_row(profile: UserProfile, fetched_at: dt.datetime) -> Row:
    return {
        "github_id": profile.github_id,
        "login": normalize_login(profile.login),
        "name": profile.name,
        "avatar_url": profile.avatar_url,
        "created_at": profile.created_at,
        "fetched_at": fetched_at,
    }


def repo_row(user_id: int, repo: RepoInfo) -> Row:
    return {
        "user_id": user_id,
        "github_id": repo.github_id,
        "name": repo.name,
        "stars": repo.stars,
        "forks": repo.forks,
        "size_kb": repo.size_kb,
        "primary_language": repo.primary_language,
        "created_at": repo.created_at,
        "pushed_at": repo.pushed_at,
        "total_commits": repo.total_commits,
    }


def language_rows(repo_id: int, languages: Mapping[str, int]) -> list[Row]:
    return [
        {"repo_id": repo_id, "language": language, "bytes": size}
        for language, size in sorted(languages.items())
    ]


def commit_rows(repo_id: int, history: CommitHistory) -> list[Row]:
    counts = daily_commit_counts(history.committed_at)
    return [
        {"repo_id": repo_id, "date": date, "count": count} for date, count in sorted(counts.items())
    ]


def contribution_rows(user_id: int, calendar: Iterable[ContributionDay]) -> list[Row]:
    # Most calendar days are zero; storing only active days keeps the table small. Readers
    # treat a missing day as zero.
    return [
        {"user_id": user_id, "date": day.date, "count": day.count}
        for day in sorted(calendar, key=lambda day: day.date)
        if day.count > 0
    ]
