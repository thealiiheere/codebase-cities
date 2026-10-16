import datetime as dt
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData
from sqlalchemy.orm import DeclarativeBase

# Giving every index and constraint a predictable name lets Alembic create, compare, and drop
# them reliably. Without this, Postgres invents names that migrations cannot refer to.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Every `Mapped[datetime]` column stores a timezone-aware timestamp (timestamptz).
    type_annotation_map: ClassVar[dict[Any, Any]] = {dt.datetime: DateTime(timezone=True)}
