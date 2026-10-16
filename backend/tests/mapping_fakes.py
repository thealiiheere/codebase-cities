"""Builders for mapping engine inputs, including random users for invariant tests."""

import datetime as dt
import random

from app.mapping import RepoData, UserData

AS_OF = dt.date(2026, 10, 16)
CREATED = dt.datetime(2015, 6, 1, tzinfo=dt.UTC)


def repo(name: str, github_id: int, **overrides: object) -> RepoData:
    fields: dict[str, object] = {
        "github_id": github_id,
        "name": name,
        "stars": 0,
        "forks": 0,
        "size_kb": 1000,
        "primary_language": "Python",
        "languages": {"Python": 1000},
        "created_at": CREATED,
        "pushed_at": dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
        "total_commits": 10,
        "daily_commits": {},
    }
    fields.update(overrides)
    return RepoData.model_validate(fields)


def user(*repos: RepoData, login: str = "octocat", **overrides: object) -> UserData:
    fields: dict[str, object] = {
        "login": login,
        "created_at": CREATED,
        "repos": repos,
        "contributions": {},
    }
    fields.update(overrides)
    return UserData.model_validate(fields)


def days_ago(days: int) -> dt.date:
    return AS_OF - dt.timedelta(days=days)


LANGUAGE_POOL = ("Python", "Go", "Rust", "TypeScript", "C", "Haskell", "Brainfork", None)


def random_user(seed: int) -> UserData:
    """A plausible random profile. Test data only: the mapping engine itself never uses this."""
    rng = random.Random(seed)
    repos = []
    for index in range(rng.randint(0, 60)):
        language = rng.choice(LANGUAGE_POOL)
        commits = int(rng.paretovariate(0.6)) - 1
        repos.append(
            repo(
                f"repo-{index}",
                10_000 + index,
                stars=int(rng.paretovariate(0.8)) - 1,
                forks=int(rng.paretovariate(1.2)) - 1,
                size_kb=rng.randint(0, 2_000_000),
                primary_language=language,
                languages={
                    language or "Text": rng.randint(1, 10**6),
                    "Shell": rng.randint(0, 10**4),
                },
                total_commits=commits,
                daily_commits={
                    days_ago(rng.randint(0, 800)): rng.randint(1, 9)
                    for _ in range(min(commits, 40))
                },
            )
        )
    contributions = {
        days_ago(rng.randint(0, 500)): rng.randint(1, 20) for _ in range(rng.randint(0, 300))
    }
    return user(*repos, login=f"user{seed}", contributions=contributions)
