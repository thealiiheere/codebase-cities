import datetime as dt

from sqlalchemy import BigInteger, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class StoredToken(Base):
    """A signed-in user's GitHub tokens, kept so their private repos can be fetched in the
    background. Both tokens are stored encrypted (see app/token_cipher.py), never in plain text,
    and are deleted when the user signs out."""

    __tablename__ = "user_tokens"

    # GitHub's account id: a user can sign in before their city was ever built.
    github_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    access_token: Mapped[str] = mapped_column(Text)
    access_expires_at: Mapped[dt.datetime | None]
    refresh_token: Mapped[str | None] = mapped_column(Text)
    refresh_expires_at: Mapped[dt.datetime | None]
    updated_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
