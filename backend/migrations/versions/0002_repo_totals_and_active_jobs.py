"""Add repos.total_commits and a one-active-job-per-username index.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repos",
        sa.Column("total_commits", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.create_index(
        "uq_jobs_username_active",
        "jobs",
        ["username"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'fetching', 'building')"),
    )


def downgrade() -> None:
    op.drop_index("uq_jobs_username_active", table_name="jobs")
    op.drop_column("repos", "total_commits")
