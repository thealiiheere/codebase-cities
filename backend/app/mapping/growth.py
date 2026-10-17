"""How a building grows: floors from commits, lit windows from recent work, and both over time.

The timeline replays a repo's history: `floor_steps` lists the day each floor was added, and
`light_steps` how lit the windows were, month by month. The frontend plays these back, so
buildings rise floor by floor as their commits land, without asking the server for each date.
"""

import calendar
import datetime as dt
from bisect import bisect_right
from itertools import accumulate

from app.mapping.inputs import RepoData
from app.mapping.scaling import log_scale

# Buildings are stacks of floors. Floors grow with commits on a log curve: 1 commit is a single
# floor, ~100 commits about 16, and 20,000 or more the full 33. (One floor per commit would turn
# a 20,000-commit repo into a 20,000-floor tower next to sheds.)
FLOOR_HEIGHT = 3.0
MAX_FLOORS = 33
COMMITS_FOR_MAX_FLOORS = 20_000

# Windows light up with commits from the last 90 days; 60 or more lights every window.
RECENT_DAYS = 90
RECENT_COMMITS_FOR_FULL_LIGHT = 60

FloorStep = tuple[dt.date, int]
LightStep = tuple[dt.date, float]


def floors_for(commits: int) -> int:
    """1 floor for 0 or 1 commits, rising on a log curve to MAX_FLOORS."""
    extra = log_scale(
        max(commits - 1, 0), saturation=COMMITS_FOR_MAX_FLOORS - 1, low=0, high=MAX_FLOORS - 1
    )
    return 1 + round(extra)


def brightness_for(recent_commits: int) -> float:
    """Share of floors with lit windows (0..1), from commits in the last RECENT_DAYS."""
    return log_scale(recent_commits, saturation=RECENT_COMMITS_FOR_FULL_LIGHT)


def floor_steps(repo: RepoData, *, as_of: dt.date) -> tuple[FloorStep, ...]:
    """(date, floors) each time the building gained floors, from its creation to `as_of`.

    The first step is the day the repo was created. Commits older than the fetched history
    (history is capped per repo) have no dates, so they count from that first day. The last
    step always reaches the building's floors for its total commit count.
    """
    created = repo.created_at.date()
    history = sorted((day, count) for day, count in repo.daily_commits.items() if day <= as_of)
    final = floors_for(repo.total_commits)
    cumulative = max(repo.total_commits - sum(count for _, count in history), 0)
    steps: list[FloorStep] = [(created, min(floors_for(cumulative), final))]
    for day, count in history:
        cumulative += count
        floors = min(floors_for(cumulative), final)
        if floors > steps[-1][1]:
            # Commits dated before the repo existed (an imported history) count from creation.
            when = max(day, created)
            if when == steps[-1][0]:
                steps[-1] = (when, floors)
            else:
                steps.append((when, floors))
    return tuple(steps)


def light_steps(repo: RepoData, *, as_of: dt.date) -> tuple[LightStep, ...]:
    """(date, brightness) at the end of each month from creation to `as_of`, only when it changes.

    Brightness is rounded to tenths: finer steps would look the same and bloat the response.
    """
    days = sorted(day for day in repo.daily_commits if day <= as_of)
    # Running totals let us count any window's commits with two binary searches, instead of
    # re-adding every day of history for every month.
    running = list(accumulate(repo.daily_commits[day] for day in days))

    def commits_through(day: dt.date) -> int:
        index = bisect_right(days, day)
        return running[index - 1] if index else 0

    steps: list[LightStep] = []
    month = repo.created_at.date().replace(day=1)
    while month <= as_of:
        last_day = month.replace(day=calendar.monthrange(month.year, month.month)[1])
        end = min(last_day, as_of)
        recent = commits_through(end) - commits_through(end - dt.timedelta(days=RECENT_DAYS))
        brightness = round(brightness_for(recent), 1)
        if not steps or brightness != steps[-1][1]:
            steps.append((end, brightness))
        month = last_day + dt.timedelta(days=1)
    return tuple(steps)
