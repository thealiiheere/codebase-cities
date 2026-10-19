import datetime as dt
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.usernames import LOGIN_MAX_LENGTH

if TYPE_CHECKING:
    from app.models.repo import Repo


class GithubUser(Base):
    __tablename__ = "github_users"
    __table_args__ = (CheckConstraint("login = lower(login)", name="login_lowercase"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # GitHub's numeric id is stable across username changes; logins are not.
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    login: Mapped[str] = mapped_column(String(LOGIN_MAX_LENGTH), unique=True)
    name: Mapped[str | None]
    avatar_url: Mapped[str]
    # When the GitHub account was created (the start of the timeline), not when this row was.
    created_at: Mapped[dt.datetime]
    # When we last ingested this user from GitHub.
    fetched_at: Mapped[dt.datetime]
    # When we last fetched the private repos they shared (null if they never did).
    private_fetched_at: Mapped[dt.datetime | None]

    # lazy="raise" turns accidental lazy loads (which fail in async code) into a clear error;
    # load these explicitly with selectinload() instead.
    repos: Mapped[list["Repo"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )
    contributions: Mapped[list["DailyContribution"]] = relationship(
        cascade="all, delete-orphan", passive_deletes=True, lazy="raise"
    )


class DailyContribution(Base):
    """One day of the user's GitHub contributions calendar."""

    __tablename__ = "daily_contributions"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("github_users.id", ondelete="CASCADE"), primary_key=True
    )
    date: Mapped[dt.date] = mapped_column(primary_key=True)
    count: Mapped[int]
