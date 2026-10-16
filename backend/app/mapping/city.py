"""Turning a user's repos into a city layout.

Coordinates are in meters on the ground plane, with the plaza at the origin: x runs east, z runs
south, and heights go up (Three.js's y axis).
"""

import datetime as dt
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from app.mapping.activity import window_total
from app.mapping.inputs import RepoData, UserData
from app.mapping.packing import Rect, place_around
from app.mapping.palette import OTHER, language_color
from app.mapping.rng import SeededRng
from app.mapping.scaling import log_scale, rounded
from app.mapping.version import MAPPING_VERSION

# --- Layout (meters) ---------------------------------------------------------------------------
PLAZA_SIZE = 30.0
AVENUE_WIDTH = 10.0  # Between districts, and between districts and the plaza.
STREET_WIDTH = 6.0  # Between blocks inside a district (wider than the gaps between lots).
SIDEWALK = 2.0  # Border inside a district's edge.
LOT_MARGIN = 2.0  # Space around a building inside its lot.
BLOCK_LOTS = 3  # A block is BLOCK_LOTS x BLOCK_LOTS lots.

# --- Buildings ---------------------------------------------------------------------------------
# Buildings are stacks of floors. Floors grow with commits on a log curve: 1 commit is a single
# floor, ~100 commits about 16, and 20,000 or more the full 33. (One floor per commit would turn
# a 20,000-commit repo into a 20,000-floor tower next to sheds.)
FLOOR_HEIGHT = 3.0
MAX_FLOORS = 33
COMMITS_FOR_MAX_FLOORS = 20_000
MIN_HEIGHT, MAX_HEIGHT = FLOOR_HEIGHT, MAX_FLOORS * FLOOR_HEIGHT
MIN_FOOTPRINT, MAX_FOOTPRINT = 4.0, 16.0
SIZE_KB_FOR_MAX_FOOTPRINT = 500_000
RECENT_DAYS = 90
RECENT_COMMITS_FOR_FULL_LIGHT = 60
MAX_ROOF_LIGHTS = 8
STYLE_COUNT = 4  # Visual variants the frontend can draw.

# --- Bridges -----------------------------------------------------------------------------------
NEIGHBORS_PER_DISTRICT = 2
FORKS_FOR_STRONGEST_BRIDGE = 1_000


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Plaza(_Frozen):
    size: float


class District(_Frozen):
    language: str
    color: str
    # Center and extent of the district's ground.
    x: float
    z: float
    width: float
    depth: float
    repo_count: int


class Building(_Frozen):
    repo: str
    district: str
    x: float
    z: float
    # Square footprint side length.
    size: float
    # Stories, growing with commits; height is always floors * FLOOR_HEIGHT.
    floors: int
    height: float
    color: str
    # 0 (dark) to 1 (every window lit), from commits in the last 90 days.
    window_brightness: float
    roof_lights: int
    style: int
    stars: int
    forks: int
    commits: int
    recent_commits: int


class Bridge(_Frozen):
    from_repo: str
    to_repo: str
    # 0 to 1, from how forked the less-forked end is.
    strength: float


class CityLayout(_Frozen):
    version: int
    username: str
    as_of: dt.date
    plaza: Plaza
    districts: tuple[District, ...]
    buildings: tuple[Building, ...]
    bridges: tuple[Bridge, ...]
    # Distance from the center to the farthest district corner, for framing the camera.
    radius: float


def build_city(user: UserData, *, as_of: dt.date, layout: UserData | None = None) -> CityLayout:
    """Lay out the city. Pure: the same inputs always give an identical layout.

    `layout` is the data the streets and plots are planned from; it defaults to `user`. The
    timeline passes the latest data here, so every repo keeps the plot it has in today's city
    and buildings never move as you scrub through the years: a repo that doesn't exist yet
    leaves its plot empty, and a district appears once its first building does.
    """
    plan = layout or user
    groups = _district_groups(plan.repos)
    grids = [_DistrictGrid.for_repos(language, repos) for language, repos in groups]
    plaza = Rect.centered(0, 0, PLAZA_SIZE, PLAZA_SIZE)
    rects = place_around(
        [(grid.width, grid.depth) for grid in grids], center=plaza, gap=AVENUE_WIDTH
    )

    present = {repo.github_id: repo for repo in user.repos}
    districts: list[District] = []
    buildings: list[Building] = []
    for grid, rect in zip(grids, rects, strict=True):
        built = grid.buildings(rect, present, user.login, as_of)
        if built:
            districts.append(grid.district(rect, len(built)))
            buildings.extend(built)

    return CityLayout(
        version=MAPPING_VERSION,
        username=user.login.lower(),
        as_of=as_of,
        plaza=Plaza(size=PLAZA_SIZE),
        districts=tuple(districts),
        buildings=tuple(buildings),
        bridges=tuple(_bridges(districts, buildings)),
        radius=rounded(_radius([plaza, *rects])),
    )


def _district_groups(repos: Sequence[RepoData]) -> list[tuple[str, list[RepoData]]]:
    """Repos grouped by primary language, biggest district first (it goes nearest the plaza)."""
    groups: dict[str, list[RepoData]] = defaultdict(list)
    for repo in repos:
        groups[repo.primary_language or OTHER].append(repo)
    for members in groups.values():
        # Most commits first: they get the lots at the district's center.
        members.sort(key=lambda r: (-r.total_commits, -r.size_kb, r.name, r.github_id))

    def importance(item: tuple[str, list[RepoData]]) -> tuple[int, int, str]:
        language, members = item
        return (-len(members), -sum(r.total_commits for r in members), language)

    return sorted(groups.items(), key=importance)


@dataclass(frozen=True)
class _DistrictGrid:
    """One district's lots, positioned relative to the district's center."""

    language: str
    repos: list[RepoData]
    lots: list[tuple[float, float]]
    width: float
    depth: float

    @classmethod
    def for_repos(cls, language: str, repos: list[RepoData]) -> "_DistrictGrid":
        # Every lot fits the district's largest building, so buildings can never overlap.
        lot = max(_footprint(repo) for repo in repos) + 2 * LOT_MARGIN
        columns = math.ceil(math.sqrt(len(repos)))
        rows = math.ceil(len(repos) / columns)
        span_x, span_z = _span(columns, lot), _span(rows, lot)
        lots = [
            (_lot_center(column, lot) - span_x / 2, _lot_center(row, lot) - span_z / 2)
            for row in range(rows)
            for column in range(columns)
        ]
        # Fill from the middle outward, so the most important repos stand at the center.
        lots.sort(key=lambda p: (round(p[0] ** 2 + p[1] ** 2, 6), p[1], p[0]))
        return cls(
            language, repos, lots[: len(repos)], span_x + 2 * SIDEWALK, span_z + 2 * SIDEWALK
        )

    def district(self, rect: Rect, repo_count: int) -> District:
        x, z = rect.center
        return District(
            language=self.language,
            color=language_color(self.language),
            x=rounded(x),
            z=rounded(z),
            width=rounded(rect.width),
            depth=rounded(rect.depth),
            repo_count=repo_count,
        )

    def buildings(
        self, rect: Rect, present: Mapping[int, RepoData], username: str, as_of: dt.date
    ) -> list[Building]:
        """A building on each lot whose repo is in `present` (by GitHub id), drawn from that
        repo's data; lots of repos that don't exist (yet) stay empty."""
        center_x, center_z = rect.center
        buildings = []
        for planned, (lot_x, lot_z) in zip(self.repos, self.lots, strict=True):
            repo = present.get(planned.github_id)
            if repo is not None:
                x, z = center_x + lot_x, center_z + lot_z
                buildings.append(_building(repo, self.language, x, z, username, as_of))
        return buildings


def _span(count: int, lot: float) -> float:
    """Length of `count` lots in a row, with a street after every full block."""
    return count * lot + (math.ceil(count / BLOCK_LOTS) - 1) * STREET_WIDTH


def _lot_center(index: int, lot: float) -> float:
    return index * lot + (index // BLOCK_LOTS) * STREET_WIDTH + lot / 2


def _footprint(repo: RepoData) -> float:
    return rounded(
        log_scale(
            repo.size_kb,
            saturation=SIZE_KB_FOR_MAX_FOOTPRINT,
            low=MIN_FOOTPRINT,
            high=MAX_FOOTPRINT,
        )
    )


def _building(
    repo: RepoData, district: str, x: float, z: float, username: str, as_of: dt.date
) -> Building:
    recent = window_total(repo.daily_commits, end=as_of, days=RECENT_DAYS)
    # A stream per repo (by GitHub's id, which survives renames), so adding a repo never
    # changes another building's style.
    style = SeededRng(username, f"building-style:{repo.github_id}").below(STYLE_COUNT)
    floors = _floors(repo.total_commits)
    return Building(
        repo=repo.name,
        district=district,
        x=rounded(x),
        z=rounded(z),
        size=_footprint(repo),
        floors=floors,
        height=rounded(floors * FLOOR_HEIGHT),
        color=language_color(district),
        window_brightness=rounded(log_scale(recent, saturation=RECENT_COMMITS_FOR_FULL_LIGHT)),
        # 1 star: 1 light, 3: 2, 7: 3, ... 255 or more: 8.
        roof_lights=min(MAX_ROOF_LIGHTS, int(math.log2(repo.stars + 1))),
        style=style,
        stars=repo.stars,
        forks=repo.forks,
        commits=repo.total_commits,
        recent_commits=recent,
    )


def _floors(commits: int) -> int:
    """1 floor for 0 or 1 commits, rising on a log curve to MAX_FLOORS."""
    extra = log_scale(
        max(commits - 1, 0), saturation=COMMITS_FOR_MAX_FLOORS - 1, low=0, high=MAX_FLOORS - 1
    )
    return 1 + round(extra)


def _bridges(districts: Sequence[District], buildings: Sequence[Building]) -> list[Bridge]:
    """Link the most-forked repo of each district to those of its nearest neighbor districts."""
    most_forked: dict[str, Building] = {}
    for building in buildings:
        best = most_forked.get(building.district)
        if building.forks > 0 and (best is None or _fork_rank(building) < _fork_rank(best)):
            most_forked[building.district] = building

    pairs: set[tuple[str, str]] = set()
    for district in districts:
        others = sorted(
            (d for d in districts if d.language != district.language),
            key=lambda d: ((d.x - district.x) ** 2 + (d.z - district.z) ** 2, d.language),
        )
        for neighbor in others[:NEIGHBORS_PER_DISTRICT]:
            a, b = sorted((district.language, neighbor.language))
            pairs.add((a, b))

    bridges = []
    for a, b in sorted(pairs):
        if a in most_forked and b in most_forked:
            ends = most_forked[a], most_forked[b]
            bridges.append(
                Bridge(
                    from_repo=ends[0].repo,
                    to_repo=ends[1].repo,
                    strength=rounded(
                        log_scale(
                            min(end.forks for end in ends), saturation=FORKS_FOR_STRONGEST_BRIDGE
                        )
                    ),
                )
            )
    return bridges


def _fork_rank(building: Building) -> tuple[int, int, str]:
    return (-building.forks, -building.commits, building.repo)


def _radius(rects: Sequence[Rect]) -> float:
    return max(
        math.hypot(x, z) for rect in rects for x in (rect.x0, rect.x1) for z in (rect.z0, rect.z1)
    )
