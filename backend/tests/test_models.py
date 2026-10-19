"""Guards for schema decisions that later phases rely on."""

from sqlalchemy import CheckConstraint, Enum, Table, UniqueConstraint

from app.models import Base, JobStatus


def table(name: str) -> Table:
    return Base.metadata.tables[name]


def primary_key(name: str) -> list[str]:
    return [column.name for column in table(name).primary_key.columns]


def test_all_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == {
        "github_users",
        "daily_contributions",
        "repos",
        "repo_languages",
        "daily_commits",
        "jobs",
        "user_tokens",
    }


def test_one_daily_commit_row_per_repo_per_day() -> None:
    assert primary_key("daily_commits") == ["repo_id", "date"]


def test_one_contribution_row_per_user_per_day() -> None:
    assert primary_key("daily_contributions") == ["user_id", "date"]


def test_one_language_row_per_repo() -> None:
    assert primary_key("repo_languages") == ["repo_id", "language"]


def test_logins_are_unique_and_lowercase() -> None:
    constraints = table("github_users").constraints
    unique_columns = {
        column.name
        for constraint in constraints
        if isinstance(constraint, UniqueConstraint)
        for column in constraint.columns
    }
    checks = {
        str(constraint.sqltext)
        for constraint in constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "login" in unique_columns
    assert "login = lower(login)" in checks


def test_job_status_is_stored_as_lowercase_value() -> None:
    status_type = table("jobs").c.status.type

    assert isinstance(status_type, Enum)
    assert status_type.enums == [status.value for status in JobStatus]
    assert status_type.enums == ["queued", "fetching", "building", "done", "failed"]
