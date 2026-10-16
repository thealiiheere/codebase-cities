import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict

from app.models import JobStatus


class JobResponse(BaseModel):
    # from_attributes lets us build this straight from a SQLAlchemy Job object.
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    status: JobStatus
    # Set when status is "failed": a message safe to show to users.
    error: str | None
    created_at: dt.datetime
    finished_at: dt.datetime | None
