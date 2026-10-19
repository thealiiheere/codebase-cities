import datetime as dt
import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, Enum, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.usernames import LOGIN_MAX_LENGTH


class JobStatus(StrEnum):
    QUEUED = "queued"
    FETCHING = "fetching"
    BUILDING = "building"
    DONE = "done"
    FAILED = "failed"


# A job in one of these states still has work to do.
ACTIVE_JOB_STATUSES = (JobStatus.QUEUED, JobStatus.FETCHING, JobStatus.BUILDING)


class Job(Base):
    """One ingestion run for a username, polled by the frontend: of their public repos, or of
    the private repos they shared (with their own token)."""

    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("username = lower(username)", name="username_lowercase"),
        # A partial unique index: at most one *active* job per username (and kind). Two
        # simultaneous requests can't both start an ingestion; the second insert fails and
        # reuses the first.
        Index(
            "uq_jobs_username_private_active",
            "username",
            "private",
            unique=True,
            postgresql_where=text("status IN ('queued', 'fetching', 'building')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(LOGIN_MAX_LENGTH), index=True)
    # Stored as VARCHAR + CHECK rather than a native Postgres ENUM, because adding a value to a
    # native ENUM later needs a special migration. values_callable stores "queued", not "QUEUED".
    status: Mapped[JobStatus] = mapped_column(
        Enum(
            JobStatus,
            name="job_status",
            native_enum=False,
            create_constraint=True,
            length=16,
            values_callable=lambda statuses: [status.value for status in statuses],
        ),
        default=JobStatus.QUEUED,
    )
    private: Mapped[bool] = mapped_column(server_default=text("false"))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[dt.datetime | None]
