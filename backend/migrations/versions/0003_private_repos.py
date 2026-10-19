"""Private repos: repos.is_private, github_users.private_fetched_at, jobs.private, user_tokens.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACTIVE = sa.text("status IN ('queued', 'fetching', 'building')")


def upgrade() -> None:
    op.add_column(
        "repos",
        sa.Column("is_private", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column(
        "github_users",
        sa.Column("private_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "jobs", sa.Column("private", sa.Boolean(), server_default=sa.text("false"), nullable=False)
    )
    # One active job per username *and kind*: a private refresh can run beside a public one.
    op.drop_index("uq_jobs_username_active", table_name="jobs")
    op.create_index(
        "uq_jobs_username_private_active",
        "jobs",
        ["username", "private"],
        unique=True,
        postgresql_where=ACTIVE,
    )
    op.create_table(
        "user_tokens",
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=False),
        sa.Column("access_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_token", sa.Text(), nullable=True),
        sa.Column("refresh_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("github_id", name=op.f("pk_user_tokens")),
    )


def downgrade() -> None:
    op.drop_table("user_tokens")
    op.drop_index("uq_jobs_username_private_active", table_name="jobs")
    # Only one active job per username may remain before the old index can return.
    op.execute(
        "UPDATE jobs SET status = 'failed', error = 'Cancelled by a downgrade.' "
        "WHERE private AND status IN ('queued', 'fetching', 'building')"
    )
    op.create_index(
        "uq_jobs_username_active", "jobs", ["username"], unique=True, postgresql_where=ACTIVE
    )
    op.drop_column("jobs", "private")
    op.drop_column("github_users", "private_fetched_at")
    # Private repos must never end up looking public.
    op.execute("DELETE FROM repos WHERE is_private")
    op.drop_column("repos", "is_private")
