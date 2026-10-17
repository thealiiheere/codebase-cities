"""The growth timeline: when each floor appears and how lit the windows are over time."""

import datetime as dt

import pytest

from app.mapping import UserData, build_city, snapshot_at
from app.mapping.growth import floor_steps, floors_for, light_steps
from tests.mapping_fakes import AS_OF, random_user, repo, user

D = dt.date
UTC = dt.UTC
CREATED = dt.datetime(2020, 1, 15, tzinfo=UTC)


def test_floors_rise_as_commits_land() -> None:
    commits = {D(2020, 1, 20): 1, D(2020, 2, 1): 5, D(2020, 6, 1): 94}
    steps = floor_steps(
        repo("r", 1, created_at=CREATED, total_commits=100, daily_commits=commits), as_of=AS_OF
    )

    assert steps[0] == (D(2020, 1, 15), 1)  # One floor the day it was created.
    assert [date for date, _ in steps] == sorted(date for date, _ in steps)
    assert [floors for _, floors in steps] == sorted({floors for _, floors in steps})  # Only rises.
    assert steps[-1] == (D(2020, 6, 1), floors_for(100))


def test_capped_history_counts_from_creation() -> None:
    # 1000 commits in total, but only 10 have dates: the other 990 predate the fetched history.
    data = repo("r", 1, created_at=CREATED, total_commits=1000, daily_commits={D(2024, 1, 1): 10})

    steps = floor_steps(data, as_of=AS_OF)

    assert steps[0] == (D(2020, 1, 15), floors_for(990))
    assert steps[-1][1] == floors_for(1000)


def test_commits_before_creation_count_from_creation() -> None:
    data = repo("r", 1, created_at=CREATED, total_commits=30, daily_commits={D(2019, 5, 1): 30})

    assert floor_steps(data, as_of=AS_OF) == ((D(2020, 1, 15), floors_for(30)),)


def test_future_commits_are_ignored() -> None:
    data = repo("r", 1, created_at=CREATED, total_commits=5, daily_commits={D(2030, 1, 1): 5})

    assert all(date <= AS_OF for date, _ in floor_steps(data, as_of=AS_OF))


def test_lights_follow_the_last_90_days() -> None:
    burst = {D(2021, 3, 10): 60}
    steps = dict(
        light_steps(repo("r", 1, created_at=CREATED, daily_commits=burst), as_of=D(2021, 12, 31))
    )

    assert steps[D(2020, 1, 31)] == 0  # Created, no commits yet.
    assert steps[D(2021, 3, 31)] == 1.0  # The burst lights every window...
    assert steps[D(2021, 6, 30)] == 0  # ...until it's more than 90 days old.
    assert all(0 <= value <= 1 for value in steps.values())


def test_light_steps_only_record_changes() -> None:
    steps = light_steps(repo("r", 1, created_at=CREATED), as_of=D(2025, 1, 1))

    assert steps == ((D(2020, 1, 31), 0.0),)


def consistent_user(seed: int) -> UserData:
    """A random profile whose dated commits never exceed a repo's total, as in real data
    (the generator in mapping_fakes doesn't guarantee that)."""
    data = random_user(seed)
    repos = tuple(
        r.model_copy(update={"total_commits": max(r.total_commits, sum(r.daily_commits.values()))})
        for r in data.repos
    )
    return data.model_copy(update={"repos": repos})


@pytest.mark.parametrize("seed", range(20))
def test_growth_matches_the_city_at_every_date(seed: int) -> None:
    """Playing a building's growth back to any date gives the floors its city had on that date."""
    data = consistent_user(seed)
    latest = build_city(data, as_of=AS_OF)
    for until in (D(2024, 6, 30), D(2025, 9, 1)):
        past = build_city(snapshot_at(data, until), as_of=until, layout=data)
        past_floors = {b.repo: b.floors for b in past.buildings}
        for building in latest.buildings:
            reached = [floors for date, floors in building.growth if date <= until]
            assert (reached[-1] if reached else 0) == past_floors.get(building.repo, 0), (
                building.repo
            )


@pytest.mark.parametrize("seed", range(20))
def test_growth_ends_at_the_buildings_floors(seed: int) -> None:
    for building in build_city(random_user(seed), as_of=AS_OF).buildings:
        assert building.growth[-1][1] == building.floors
        # Light steps are rounded to tenths, window_brightness to hundredths.
        assert abs(building.lights[-1][1] - building.window_brightness) <= 0.05 + 1e-9


def test_payload_dates_are_iso_strings() -> None:
    city = build_city(
        user(repo("r", 1, created_at=CREATED, total_commits=3, daily_commits={D(2020, 2, 1): 3})),
        as_of=AS_OF,
    )

    payload = city.model_dump(mode="json")["buildings"][0]
    assert payload["growth"][0] == ["2020-01-15", 1]
    assert isinstance(payload["lights"][0][0], str)
