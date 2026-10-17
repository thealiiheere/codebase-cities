"""Regenerate the frontend's demo data: a city (and its music over time) from a made-up profile.

Writes frontend/public/demo-city.json (today's city, which holds every building's growth over
time) and demo-music.json (the music at the end of every earlier year). The frontend's demo mode
(/?demo) loads them, so the 3D city, the music, and the timeline all work without a running
backend. Run from backend/ whenever the mapping engine changes:

    uv run python -m scripts.export_demo_city
"""

import datetime as dt
import json
import random
from pathlib import Path

from app.mapping import RepoData, UserData
from app.services.city_payload import build_payload

PUBLIC = Path(__file__).resolve().parents[2] / "frontend" / "public"
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
            # Spread every commit between creation and the last push: most across the repo's
            # whole life (so the timeline shows buildings growing), some bunched near the last
            # push. The daily counts add up to the repo's total, exactly like real data.
            span = (last_push - created).days
            daily: dict[dt.date, int] = {}
            for _ in range(commits):
                if rng.random() < 0.7:
                    back = int(rng.random() * span)
                else:
                    back = min(int(rng.expovariate(1 / 90)), span)
                day = last_push - dt.timedelta(days=back)
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
    profile = demo_profile()
    PUBLIC.mkdir(parents=True, exist_ok=True)

    latest = build_payload(profile, FETCHED_AT, until=None)
    (PUBLIC / "demo-city.json").write_text(latest.model_dump_json(), encoding="utf-8")
    city = latest.city
    print(
        f"demo-city.json: {len(city.buildings)} buildings in {len(city.districts)} districts, "
        f"{len(city.bridges)} bridges, {sum(b.floors for b in city.buildings)} floors"
    )

    # Keyed like the frontend's requests to the API: the last day of each year before this one.
    years = range(profile.created_at.year, FETCHED_AT.year)
    music = {
        f"{year}-12-31": build_payload(profile, FETCHED_AT, dt.date(year, 12, 31)).music.model_dump(
            mode="json"
        )
        for year in years
    }
    (PUBLIC / "demo-music.json").write_text(
        json.dumps(music, separators=(",", ":")), encoding="utf-8"
    )
    tracks = ", ".join(f"{date[:4]}: {len(m['tracks'])}" for date, m in music.items())
    print(f"demo-music.json: tracks per year - {tracks}")


if __name__ == "__main__":
    main()
