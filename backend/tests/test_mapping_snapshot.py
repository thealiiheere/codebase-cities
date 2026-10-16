"""The timeline: snapshot_at(user, until)."""

import datetime as dt

from app.mapping import build_city, snapshot_at
from tests.mapping_fakes import repo as make_repo
from tests.mapping_fakes import user

D = dt.date
UTC = dt.UTC


def test_leaves_out_repos_created_later() -> None:
    data = user(
        make_repo("old", 1, created_at=dt.datetime(2018, 1, 1, tzinfo=UTC)),
        make_repo("same-day", 2, created_at=dt.datetime(2020, 6, 1, 23, 59, tzinfo=UTC)),
        make_repo("new", 3, created_at=dt.datetime(2020, 6, 2, tzinfo=UTC)),
    )

    snapshot = snapshot_at(data, D(2020, 6, 1))

    assert [r.name for r in snapshot.repos] == ["old", "same-day"]


def test_commit_totals_and_history_stop_at_until() -> None:
    data = user(
        make_repo(
            "r",
            1,
            total_commits=100,
            daily_commits={D(2020, 1, 1): 5, D(2020, 6, 1): 7, D(2021, 1, 1): 20},
        )
    )

    [repo] = snapshot_at(data, D(2020, 6, 1)).repos

    assert repo.total_commits == 80  # 100 minus the 20 known commits after until.
    assert repo.daily_commits == {D(2020, 1, 1): 5, D(2020, 6, 1): 7}


def test_totals_never_go_negative() -> None:
    data = user(make_repo("r", 1, total_commits=3, daily_commits={D(2021, 1, 1): 9}))

    assert snapshot_at(data, D(2020, 1, 1)).repos[0].total_commits == 0


def test_contributions_and_pushes_stop_at_until() -> None:
    data = user(
        make_repo("r", 1, pushed_at=dt.datetime(2022, 1, 1, tzinfo=UTC)),
        contributions={D(2020, 1, 1): 1, D(2022, 1, 1): 1},
    )

    snapshot = snapshot_at(data, D(2021, 1, 1))

    assert snapshot.contributions == {D(2020, 1, 1): 1}
    assert snapshot.repos[0].pushed_at is None


def test_the_city_grows_over_time() -> None:
    data = user(
        *(
            make_repo(
                f"r{year}",
                year,
                created_at=dt.datetime(year, 1, 1, tzinfo=UTC),
                total_commits=500,
                daily_commits={D(year, 3, 1): 100, D(2025, 3, 1): 100},
            )
            for year in range(2016, 2026)
        )
    )

    sizes = []
    for year in (2016, 2019, 2022, 2025):
        until = D(year, 12, 31)
        city = build_city(snapshot_at(data, until), as_of=until)
        sizes.append((len(city.buildings), sum(b.height for b in city.buildings)))

    assert [count for count, _ in sizes] == [1, 4, 7, 10]
    assert [height for _, height in sizes] == sorted(height for _, height in sizes)
