"""Step 3: write a user's fetched data to PostgreSQL, replacing what was there.

Call these inside a single transaction: readers then see either the old data or the new data,
never a half-written mix. Public and private repos are refreshed separately (by different jobs,
with different tokens), so each only ever replaces its own kind.
"""

import datetime as dt
from collections.abc import Sequence

from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import ColumnElement

from app.github.models import CommitHistory, RepoInfo
from app.ingestion.fetch import FetchedPrivateRepos, FetchedUserData
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
    # A repo whose visibility changed moves to the other kind.
    "is_private",
)


async def store_user_data(
    session: AsyncSession, data: FetchedUserData, *, fetched_at: dt.datetime
) -> int:
    """Store everything for one user and return their database id."""
    user_id = await _upsert_user(session, user_row(data.profile, fetched_at))
    await _store_repos(session, user_id, data.repos, data.histories, is_private=False)
    contributions = contribution_rows(user_id, data.calendar)
    await _replace(session, DailyContribution, DailyContribution.user_id == user_id, contributions)
    return user_id


async def store_private_repos(
    session: AsyncSession, user_id: int, data: FetchedPrivateRepos, *, fetched_at: dt.datetime
) -> None:
    """Replace the private repos a user shared, for the github_users row `user_id`."""
    await _store_repos(session, user_id, data.repos, data.histories, is_private=True)
    await session.execute(
        update(GithubUser).where(GithubUser.id == user_id).values(private_fetched_at=fetched_at)
    )


async def delete_private_repos(session: AsyncSession, github_id: int) -> None:
    """Forget the private repos a user shared (when they sign out)."""
    user_id = select(GithubUser.id).where(GithubUser.github_id == github_id).scalar_subquery()
    await session.execute(delete(Repo).where(Repo.user_id == user_id, Repo.is_private))
    await session.execute(
        update(GithubUser).where(GithubUser.github_id == github_id).values(private_fetched_at=None)
    )


async def _store_repos(
    session: AsyncSession,
    user_id: int,
    repos: list[RepoInfo],
    histories: dict[str, CommitHistory],
    *,
    is_private: bool,
) -> None:
    rows = [repo_row(user_id, repo, is_private=is_private) for repo in repos]
    repo_ids = await _upsert_repos(session, rows)
    ids = list(repo_ids.values())

    # Repos of this kind that were deleted, changed visibility, stopped being shared, or dropped
    # out of the top N disappear, with their languages and commits (ON DELETE CASCADE).
    await session.execute(
        delete(Repo).where(
            Repo.user_id == user_id, Repo.is_private.is_(is_private), Repo.id.not_in(ids)
        )
    )

    languages = [row for r in repos for row in language_rows(repo_ids[r.name], r.languages)]
    await _replace(session, RepoLanguage, RepoLanguage.repo_id.in_(ids), languages)

    commits = [
        row
        for name, history in histories.items()
        if name in repo_ids
        for row in commit_rows(repo_ids[name], history)
    ]
    await _replace(session, DailyCommit, DailyCommit.repo_id.in_(ids), commits)


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
