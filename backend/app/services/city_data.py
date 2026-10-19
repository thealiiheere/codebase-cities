"""Loading a user's stored GitHub data into the mapping engine's input shape."""

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.mapping import RepoData, UserData
from app.models import DailyCommit, DailyContribution, GithubUser, Repo, RepoLanguage


@dataclass(frozen=True)
class StoredUser:
    data: UserData
    fetched_at: dt.datetime


async def load_user_data(
    session: AsyncSession, login: str, *, include_private: bool = False
) -> StoredUser | None:
    """Everything stored for one (lowercase) login, or None if it was never ingested.

    Private repos are left out unless `include_private` (only ever for the owner's own city).
    Five small queries (user, repos, languages, commits, contributions) instead of one big join,
    which would repeat every repo row once per language per day.
    """
    user = await session.scalar(select(GithubUser).where(GithubUser.login == login))
    if user is None:
        return None
    query = select(Repo).where(Repo.user_id == user.id)
    if not include_private:
        query = query.where(Repo.is_private.is_(False))
    repos = (await session.scalars(query)).all()
    repo_ids = [repo.id for repo in repos]

    languages: dict[int, dict[str, int]] = defaultdict(dict)
    language_rows = await session.execute(
        select(RepoLanguage.repo_id, RepoLanguage.language, RepoLanguage.bytes).where(
            RepoLanguage.repo_id.in_(repo_ids)
        )
    )
    for repo_id, language, size in language_rows:
        languages[repo_id][language] = size

    commits: dict[int, dict[dt.date, int]] = defaultdict(dict)
    commit_rows = await session.execute(
        select(DailyCommit.repo_id, DailyCommit.date, DailyCommit.count).where(
            DailyCommit.repo_id.in_(repo_ids)
        )
    )
    for repo_id, date, count in commit_rows:
        commits[repo_id][date] = count

    contribution_rows = await session.execute(
        select(DailyContribution.date, DailyContribution.count).where(
            DailyContribution.user_id == user.id
        )
    )

    data = UserData(
        login=user.login,
        created_at=user.created_at,
        repos=tuple(
            RepoData(
                github_id=repo.github_id,
                name=repo.name,
                stars=repo.stars,
                forks=repo.forks,
                size_kb=repo.size_kb,
                primary_language=repo.primary_language,
                languages=languages[repo.id],
                created_at=repo.created_at,
                pushed_at=repo.pushed_at,
                total_commits=repo.total_commits,
                daily_commits=commits[repo.id],
                private=repo.is_private,
            )
            for repo in repos
        ),
        contributions={date: count for date, count in contribution_rows},
    )
    return StoredUser(data=data, fetched_at=user.fetched_at)
