"""Step 3: write a user's fetched data to PostgreSQL, replacing what was there.

Call store_user_data inside a single transaction: readers then see either the old data or the
new data, never a half-written mix.
"""

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import delete, insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.ingestion.fetch import FetchedUserData
from app.ingestion.normalize import (
    Row,
    commit_rows,
    contribution_rows,
    language_rows,
    repo_row,
    user_row,
)
from app.models import Base, DailyCommit, DailyContribution, GithubUser, Repo, RepoLanguage

# Columns refreshed when a row already exists (everything except the keys we match on).
_USER_UPDATE_COLUMNS = ("login", "name", "avatar_url", "created_at", "fetched_at")
_REPO_UPDATE_COLUMNS = (
    "user_id",
    "name",
    "stars",
    "forks",
    "size_kb",
    "primary_language",
    "created_at",
    "pushed_at",
    "total_commits",
)


async def store_user_data(
    session: AsyncSession, data: FetchedUserData, *, fetched_at: dt.datetime
) -> int:
    """Store everything for one user and return their database id."""
    user_id = await _upsert_user(session, user_row(data.profile, fetched_at))
    repo_ids = await _upsert_repos(session, [repo_row(user_id, repo) for repo in data.repos])
    ids = list(repo_ids.values())

    # Repos that were deleted, made private, or dropped out of the top N disappear, along with
    # their languages and commits (ON DELETE CASCADE).
    await session.execute(delete(Repo).where(Repo.user_id == user_id, Repo.id.not_in(ids)))

    languages = [row for r in data.repos for row in language_rows(repo_ids[r.name], r.languages)]
    await _replace(session, RepoLanguage, RepoLanguage.repo_id.in_(ids), languages)

    commits = [
        row
        for name, history in data.histories.items()
        if name in repo_ids
        for row in commit_rows(repo_ids[name], history)
    ]
    await _replace(session, DailyCommit, DailyCommit.repo_id.in_(ids), commits)

    contributions = contribution_rows(user_id, data.calendar)
    await _replace(session, DailyContribution, DailyContribution.user_id == user_id, contributions)
    return user_id


async def _upsert_user(session: AsyncSession, row: Row) -> int:
    # If a *different* GitHub account held this login before (renamed, then the name was taken),
    # its stale row would block the unique login, so remove it first.
    await session.execute(
        delete(GithubUser).where(
            GithubUser.login == row["login"], GithubUser.github_id != row["github_id"]
        )
    )
    insert_user = pg_insert(GithubUser).values(row)
    upsert = insert_user.on_conflict_do_update(
        index_elements=[GithubUser.github_id],
        set_={column: insert_user.excluded[column] for column in _USER_UPDATE_COLUMNS},
    ).returning(GithubUser.id)
    return (await session.execute(upsert)).scalar_one()


async def _upsert_repos(session: AsyncSession, rows: list[Row]) -> dict[str, int]:
    """Insert or update repos, matched on GitHub's id. Returns {repo name: database id}."""
    if not rows:
        return {}
    insert_repos = pg_insert(Repo).values(rows)
    upsert = insert_repos.on_conflict_do_update(
        index_elements=[Repo.github_id],
        set_={column: insert_repos.excluded[column] for column in _REPO_UPDATE_COLUMNS},
    ).returning(Repo.id, Repo.name)
    result = await session.execute(upsert)
    return {name: repo_id for repo_id, name in result.all()}


async def _replace(
    session: AsyncSession, model: type[Base], condition: ColumnElement[bool], rows: Sequence[Row]
) -> None:
    """Delete the rows matching `condition`, then insert `rows` in efficient batches."""
    await session.execute(delete(model).where(condition))
    if rows:
        await session.execute(insert(model), rows)
