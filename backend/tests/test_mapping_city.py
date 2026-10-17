"""City layout: building scales, districts, bridges, and layout invariants on random users."""

import datetime as dt

import pytest

from app.mapping import CityLayout, build_city
from app.mapping.city import (
    AVENUE_WIDTH,
    MAX_FOOTPRINT,
    MAX_HEIGHT,
    MAX_ROOF_LIGHTS,
    MIN_FOOTPRINT,
    MIN_HEIGHT,
    PLAZA_SIZE,
    STYLE_COUNT,
)
from app.mapping.growth import FLOOR_HEIGHT
from app.mapping.packing import Rect
from app.mapping.palette import OTHER
from tests.mapping_fakes import AS_OF, days_ago, random_user, repo, user


def city_of(*repos: object, **user_fields: object) -> CityLayout:
    return build_city(user(*repos, **user_fields), as_of=AS_OF)  # type: ignore[arg-type]


def by_name(city: CityLayout) -> dict[str, object]:
    return {b.repo: b for b in city.buildings}


# --- Buildings ---------------------------------------------------------------------------------


def test_more_commits_means_taller_within_limits() -> None:
    city = city_of(
        *(repo(f"r{c}", c, total_commits=c) for c in (0, 1, 10, 1000, 10**6)),
    )
    heights = [b.height for b in sorted(city.buildings, key=lambda b: b.commits)]

    assert heights == sorted(heights)
    assert heights[0] == MIN_HEIGHT
    assert heights[-1] == MAX_HEIGHT


@pytest.mark.parametrize(
    ("commits", "floors"),
    [(0, 1), (1, 1), (10, 8), (100, 16), (1000, 23), (20_000, 33), (10**6, 33)],
)
def test_buildings_are_whole_floors_that_grow_with_commits(commits: int, floors: int) -> None:
    building = city_of(repo("r", 1, total_commits=commits)).buildings[0]

    assert building.floors == floors
    assert building.height == floors * FLOOR_HEIGHT


def test_bigger_repos_have_bigger_footprints_within_limits() -> None:
    city = city_of(*(repo(f"r{s}", s + 1, size_kb=s) for s in (0, 100, 10**5, 10**9)))
    sizes = [b.size for b in sorted(city.buildings, key=lambda b: b.repo)]

    assert min(sizes) == MIN_FOOTPRINT
    assert max(sizes) == MAX_FOOTPRINT
    assert len(set(sizes)) == 4


def test_windows_reflect_only_the_last_90_days() -> None:
    city = city_of(
        repo("busy", 1, daily_commits={days_ago(0): 30, days_ago(89): 30}),
        repo("old", 2, daily_commits={days_ago(90): 50, days_ago(400): 50}),
        repo("future", 3, daily_commits={AS_OF + dt.timedelta(days=1): 99}),
    )
    buildings = {b.repo: b for b in city.buildings}

    assert buildings["busy"].recent_commits == 60
    assert buildings["busy"].window_brightness == 1.0
    assert buildings["old"].window_brightness == 0.0
    assert buildings["future"].recent_commits == 0  # Days after as_of don't exist yet.


def test_window_brightness_depends_on_as_of_not_on_today() -> None:
    data = user(repo("r", 1, daily_commits={dt.date(2020, 1, 1): 10}))

    then = build_city(data, as_of=dt.date(2020, 1, 15)).buildings[0]
    later = build_city(data, as_of=dt.date(2021, 1, 1)).buildings[0]

    assert then.window_brightness > 0
    assert later.window_brightness == 0


@pytest.mark.parametrize(
    ("stars", "lights"), [(0, 0), (1, 1), (2, 1), (3, 2), (7, 3), (255, 8), (10**6, 8)]
)
def test_rooftop_lights_scale_with_stars(stars: int, lights: int) -> None:
    assert city_of(repo("r", 1, stars=stars)).buildings[0].roof_lights == lights


# --- Districts ---------------------------------------------------------------------------------


def test_repos_are_grouped_into_language_districts() -> None:
    city = city_of(
        repo("a", 1, primary_language="Go"),
        repo("b", 2, primary_language="Go"),
        repo("c", 3, primary_language=None),
    )

    assert {d.language: d.repo_count for d in city.districts} == {"Go": 2, OTHER: 1}
    assert {b.repo: b.district for b in city.buildings} == {"a": "Go", "b": "Go", "c": OTHER}
    go = next(d for d in city.districts if d.language == "Go")
    assert all(b.color == go.color for b in city.buildings if b.district == "Go")


def test_bigger_districts_sit_nearer_the_plaza() -> None:
    repos = [repo(f"py{i}", i, primary_language="Python") for i in range(9)]
    repos += [repo(f"go{i}", 100 + i, primary_language="Go") for i in range(4)]
    repos += [repo("hs", 200, primary_language="Haskell")]
    city = city_of(*repos)

    # Gap between each district's edge and the plaza's edge.
    half = PLAZA_SIZE / 2
    gap = {
        d.language: max(abs(d.x) - d.width / 2 - half, abs(d.z) - d.depth / 2 - half)
        for d in city.districts
    }
    assert city.districts[0].language == "Python"
    assert gap["Python"] == pytest.approx(AVENUE_WIDTH)  # Right across the avenue.
    assert gap["Python"] <= gap["Go"] + 1e-6 <= gap["Haskell"] + 2e-6


def test_most_committed_repo_stands_at_the_district_center() -> None:
    city = city_of(*(repo(f"r{i}", i, total_commits=i * 10) for i in range(1, 10)))
    district = city.districts[0]
    nearest = min(city.buildings, key=lambda b: (b.x - district.x) ** 2 + (b.z - district.z) ** 2)

    assert nearest.repo == "r9"


def test_empty_profile_is_just_a_plaza() -> None:
    city = city_of()

    assert (city.districts, city.buildings, city.bridges) == ((), (), ())
    assert city.plaza.size == PLAZA_SIZE
    assert city.radius > 0


# --- Bridges -----------------------------------------------------------------------------------


def test_bridges_link_the_most_forked_repos_of_neighboring_districts() -> None:
    city = city_of(
        repo("py-small", 1, primary_language="Python", forks=2),
        repo("py-star", 2, primary_language="Python", forks=50),
        repo("go-star", 3, primary_language="Go", forks=8),
        repo("rs-unforked", 4, primary_language="Rust", forks=0),
    )

    assert [(b.from_repo, b.to_repo) for b in city.bridges] == [("go-star", "py-star")]
    assert 0 < city.bridges[0].strength < 1


def test_no_bridges_without_forks_or_neighbors() -> None:
    assert city_of(repo("a", 1, forks=9), repo("b", 2, forks=9)).bridges == ()
    assert city_of(repo("a", 1, primary_language="Go"), repo("b", 2)).bridges == ()


# --- Invariants on many random profiles --------------------------------------------------------

RANDOM_USERS = [random_user(seed) for seed in range(50)]


@pytest.mark.parametrize("data", RANDOM_USERS, ids=lambda u: u.login)
def test_layout_invariants(data: object) -> None:
    city = build_city(data, as_of=AS_OF)  # type: ignore[arg-type]
    plaza = Rect.centered(0, 0, PLAZA_SIZE, PLAZA_SIZE)
    districts = {d.language: Rect.centered(d.x, d.z, d.width, d.depth) for d in city.districts}
    footprints = [Rect.centered(b.x, b.z, b.size, b.size) for b in city.buildings]

    # Every repo becomes exactly one building.
    assert sorted(b.repo for b in city.buildings) == sorted(r.name for r in data.repos)  # type: ignore[attr-defined]
    # Bigger districts (listed first) are never farther from the plaza than smaller ones.
    gaps = [round(rect.distance_to(plaza), 1) for rect in districts.values()]
    assert gaps == sorted(gaps)
    # Districts never overlap each other or the plaza.
    rects = [plaza, *districts.values()]
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            assert not a.overlaps(b), (a, b)
    # Buildings never overlap, and each stands inside its own district.
    for i, a in enumerate(footprints):
        for b in footprints[i + 1 :]:
            assert not a.overlaps(b), (a, b)
    for building, footprint in zip(city.buildings, footprints, strict=True):
        district = districts[building.district]
        assert district.x0 <= footprint.x0 and footprint.x1 <= district.x1
        assert district.z0 <= footprint.z0 and footprint.z1 <= district.z1
    # Every value stays in its documented range.
    for building in city.buildings:
        assert MIN_HEIGHT <= building.height <= MAX_HEIGHT
        assert MIN_FOOTPRINT <= building.size <= MAX_FOOTPRINT
        assert 0 <= building.window_brightness <= 1
        assert 0 <= building.roof_lights <= MAX_ROOF_LIGHTS
        assert 0 <= building.style < STYLE_COUNT
    for bridge in city.bridges:
        assert 0 <= bridge.strength <= 1
    # The camera radius covers every district.
    for rect in districts.values():
        for x in (rect.x0, rect.x1):
            for z in (rect.z0, rect.z1):
                assert (x**2 + z**2) ** 0.5 <= city.radius + 0.01
