import datetime as dt
import uuid
from enum import StrEnum

from sqlalchemy import CheckConstraint, Enum, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.user import LOGIN_MAX_LENGTH


class JobStatus(StrEnum):
    QUEUED = "queued"
    FETCHING = "fetching"
    BUILDING = "building"
    DONE = "done"
    FAILED = "failed"


class Job(Base):
    """One ingestion run for a username, polled by the frontend."""

    __tablename__ = "jobs"
    __table_args__ = (CheckConstraint("username = lower(username)", name="username_lowercase"),)

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
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
    finished_at: Mapped[dt.datetime | None]
