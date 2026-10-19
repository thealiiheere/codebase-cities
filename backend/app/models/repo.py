import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, ForeignKey, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import GithubUser

# GitHub caps repository names at 100 characters.
REPO_NAME_MAX_LENGTH = 100


class Repo(Base):
    """A non-fork repository owned by a GitHub user: public, or private and shared with us by
    its owner (shown only to them)."""

    __tablename__ = "repos"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("github_users.id", ondelete="CASCADE"), index=True
    )
    # BigInteger because GitHub ids are approaching the 32-bit limit.
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    name: Mapped[str] = mapped_column(String(REPO_NAME_MAX_LENGTH))
    stars: Mapped[int]
    forks: Mapped[int]
    size_kb: Mapped[int]
    primary_language: Mapped[str | None]
    created_at: Mapped[dt.datetime]
    # Null for repos that have never been pushed to.
    pushed_at: Mapped[dt.datetime | None]
    # Every commit on the default branch. daily_commits can hold fewer, because fetched history
    # is capped per repo, so use this for anything that needs the true total.
    total_commits: Mapped[int] = mapped_column(server_default=text("0"))
    # Private repos come from the owner's own token and are only ever shown to the owner.
    is_private: Mapped[bool] = mapped_column(server_default=text("false"))

    user: Mapped["GithubUser"] = relationship(back_populates="repos", lazy="raise")
    languages: Mapped[list["RepoLanguage"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )
    daily_commits: Mapped[list["DailyCommit"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )


class RepoLanguage(Base):
    """Bytes of code per language in a repo, as reported by GitHub's linguist."""

    __tablename__ = "repo_languages"

    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repos.id", ondelete="CASCADE"), primary_key=True
    )
    language: Mapped[str] = mapped_column(primary_key=True)
    bytes: Mapped[int] = mapped_column(BigInteger)


class DailyCommit(Base):
    """Commits on a repo's default branch for one day. Days with no commits have no row."""

    __tablename__ = "daily_commits"

    # The composite primary key also enforces "one row per repo per day".
    repo_id: Mapped[int] = mapped_column(
        ForeignKey("repos.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[dt.date] = mapped_column(primary_key=True)
    count: Mapped[int]
