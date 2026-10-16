"""Regenerate frontend/public/demo-city.json: a city built from a made-up profile.

The frontend's demo mode (/?demo) loads that file, so the 3D city can be developed and shown
without a running backend. Run from backend/ whenever the mapping engine changes:

    uv run python -m scripts.export_demo_city
"""

import datetime as dt
import random
from pathlib import Path

from app.mapping import RepoData, UserData
from app.services.city_payload import build_payload

OUTPUT = Path(__file__).resolve().parents[2] / "frontend" / "public" / "demo-city.json"
FETCHED_AT = dt.datetime(2026, 10, 16, 12, 0, tzinfo=dt.UTC)

# (language, number of repos): a polyglot developer, mostly Python and TypeScript.
LANGUAGES = [
    ("Python", 26),
    ("TypeScript", 18),
    ("Go", 11),
    ("Rust", 8),
    ("C", 6),
    ("Shell", 6),
    ("HTML", 5),
    ("Haskell", 4),
    ("Lua", 3),
    ("Zig", 2),
    ("Elm", 1),
    (None, 2),
]


def demo_profile() -> UserData:
    # A fixed seed, so the demo city is the same every time the script runs.
    rng = random.Random(2026)
    today = FETCHED_AT.date()
    repos: list[RepoData] = []
    for language, count in LANGUAGES:
        for _ in range(count):
            index = len(repos)
            created = today - dt.timedelta(days=rng.randint(30, 3000))
            # Log-normal: most repos have tens to hundreds of commits, a few have thousands.
            commits = max(1, int(rng.lognormvariate(4.0, 1.4)))
            # Recent activity for about a third of the repos, so some windows are lit.
            active = rng.random() < 0.35
            history_days = rng.randint(0, 80) if active else rng.randint(120, 1500)
            last_push = max(today - dt.timedelta(days=history_days), created)
            # Spread every commit between creation and the last push, most of them near the
            # push, so the daily counts add up to the repo's total exactly like real data.
            span = (last_push - created).days
            daily: dict[dt.date, int] = {}
            for _ in range(commits):
                day = last_push - dt.timedelta(days=min(int(rng.expovariate(1 / 90)), span))
                daily[day] = daily.get(day, 0) + 1
            name = f"{(language or 'notes').lower()}-{_project_word(rng)}-{index}"
            repos.append(
                RepoData(
                    github_id=900_000 + index,
                    name=name,
                    stars=max(0, int(rng.paretovariate(0.7)) - 1),
                    forks=max(0, int(rng.paretovariate(1.1)) - 1),
                    size_kb=rng.randint(20, 800_000),
                    primary_language=language,
                    languages={language or "Markdown": rng.randint(5_000, 2_000_000)},
                    created_at=dt.datetime.combine(created, dt.time(12), dt.UTC),
                    pushed_at=dt.datetime.combine(last_push, dt.time(18), dt.UTC),
                    total_commits=commits,
                    daily_commits=daily,
                )
            )
    contributions = {
        today - dt.timedelta(days=day): rng.randint(1, 12)
        for day in range(0, 700)
        if rng.random() < (0.6 if day < 120 else 0.25)
    }
    return UserData(
        login="demo-developer",
        created_at=dt.datetime(2016, 3, 1, tzinfo=dt.UTC),
        repos=tuple(repos),
        contributions=contributions,
    )


def _project_word(rng: random.Random) -> str:
    return rng.choice(
        ["api", "cli", "engine", "toolkit", "lab", "bot", "parser", "server", "ui", "sdk", "lib"]
    )


def main() -> None:
    payload = build_payload(demo_profile(), FETCHED_AT, until=None)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(payload.model_dump_json(), encoding="utf-8")
    city = payload.city
    print(
        f"Wrote {OUTPUT}: {len(city.buildings)} buildings in {len(city.districts)} districts, "
        f"{len(city.bridges)} bridges, {sum(b.floors for b in city.buildings)} floors"
    )


if __name__ == "__main__":
    main()
