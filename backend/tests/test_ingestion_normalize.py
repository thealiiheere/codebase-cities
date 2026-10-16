import datetime as dt

from app.github.models import ContributionDay
from app.ingestion.normalize import (
    commit_rows,
    contribution_rows,
    daily_commit_counts,
    language_rows,
    user_row,
)
from tests.fakes import T0, make_history, make_profile

UTC = dt.UTC


def test_counts_commits_per_utc_day() -> None:
    plus_five = dt.timezone(dt.timedelta(hours=5))
    counts = daily_commit_counts(
        [
            dt.datetime(2026, 3, 1, 9, tzinfo=UTC),
            dt.datetime(2026, 3, 1, 23, 59, tzinfo=UTC),
            # 02:00 on March 2nd at UTC+5 is still March 1st in UTC.
            dt.datetime(2026, 3, 2, 2, tzinfo=plus_five),
            dt.datetime(2026, 3, 2, 0, 0, tzinfo=UTC),
        ]
    )

    assert counts == {dt.date(2026, 3, 1): 3, dt.date(2026, 3, 2): 1}


def test_no_commits_means_no_days() -> None:
    assert daily_commit_counts([]) == {}


def test_user_row_lowercases_login() -> None:
    row = user_row(make_profile(login="OctoCat"), fetched_at=T0)

    assert row["login"] == "octocat"
    assert row["fetched_at"] == T0


def test_commit_rows_are_sorted_daily_aggregates() -> None:
    history = make_history(
        "r",
        dt.datetime(2026, 3, 2, 8, tzinfo=UTC),
        dt.datetime(2026, 3, 1, 8, tzinfo=UTC),
        dt.datetime(2026, 3, 2, 9, tzinfo=UTC),
    )

    assert commit_rows(7, history) == [
        {"repo_id": 7, "date": dt.date(2026, 3, 1), "count": 1},
        {"repo_id": 7, "date": dt.date(2026, 3, 2), "count": 2},
    ]


def test_contribution_rows_skip_quiet_days() -> None:
    calendar = [
        ContributionDay(date=dt.date(2026, 1, 2), count=4),
        ContributionDay(date=dt.date(2026, 1, 1), count=0),
        ContributionDay(date=dt.date(2026, 1, 3), count=1),
    ]

    assert contribution_rows(5, calendar) == [
        {"user_id": 5, "date": dt.date(2026, 1, 2), "count": 4},
        {"user_id": 5, "date": dt.date(2026, 1, 3), "count": 1},
    ]


def test_language_rows() -> None:
    assert language_rows(3, {"Rust": 10, "C": 5}) == [
        {"repo_id": 3, "language": "C", "bytes": 5},
        {"repo_id": 3, "language": "Rust", "bytes": 10},
    ]
