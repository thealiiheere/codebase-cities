"""Initial schema: users, repos, languages, daily commits, contributions, jobs.

Revision ID: 0001
Revises:
Create Date: 2026-10-16

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JOB_STATUSES = ("queued", "fetching", "building", "done", "failed")


def upgrade() -> None:
    op.create_table(
        "github_users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("login", sa.String(length=39), nullable=False),
        sa.Column("name", sa.String(), nullable=True),
        sa.Column("avatar_url", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("login = lower(login)", name=op.f("ck_github_users_login_lowercase")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_github_users")),
        sa.UniqueConstraint("github_id", name=op.f("uq_github_users_github_id")),
        sa.UniqueConstraint("login", name=op.f("uq_github_users_login")),
    )
    op.create_table(
        "daily_contributions",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["github_users.id"],
            name=op.f("fk_daily_contributions_user_id_github_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("user_id", "date", name=op.f("pk_daily_contributions")),
    )
    op.create_table(
        "repos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("github_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("stars", sa.Integer(), nullable=False),
        sa.Column("forks", sa.Integer(), nullable=False),
        sa.Column("size_kb", sa.Integer(), nullable=False),
        sa.Column("primary_language", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("pushed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["github_users.id"],
            name=op.f("fk_repos_user_id_github_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_repos")),
        sa.UniqueConstraint("github_id", name=op.f("uq_repos_github_id")),
    )
    op.create_index(op.f("ix_repos_user_id"), "repos", ["user_id"])
    op.create_table(
        "repo_languages",
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(), nullable=False),
        sa.Column("bytes", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["repo_id"],
            ["repos.id"],
            name=op.f("fk_repo_languages_repo_id_repos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("repo_id", "language", name=op.f("pk_repo_languages")),
    )
    op.create_table(
        "daily_commits",
        sa.Column("repo_id", sa.Integer(), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["repo_id"],
            ["repos.id"],
            name=op.f("fk_daily_commits_repo_id_repos"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("repo_id", "date", name=op.f("pk_daily_commits")),
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(length=39), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("username = lower(username)", name=op.f("ck_jobs_username_lowercase")),
        sa.CheckConstraint(
            f"status IN ({', '.join(repr(s) for s in JOB_STATUSES)})",
            name=op.f("ck_jobs_job_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jobs")),
    )
    op.create_index(op.f("ix_jobs_username"), "jobs", ["username"])


def downgrade() -> None:
    op.drop_index(op.f("ix_jobs_username"), table_name="jobs")
    op.drop_table("jobs")
    op.drop_table("daily_commits")
    op.drop_table("repo_languages")
    op.drop_index(op.f("ix_repos_user_id"), table_name="repos")
    op.drop_table("repos")
    op.drop_table("daily_contributions")
    op.drop_table("github_users")
