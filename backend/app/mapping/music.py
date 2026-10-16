"""Turning a user's activity and languages into music parameters (the frontend plays them)."""

import datetime as dt
import math
from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict

from app.mapping.activity import burstiness, daily_series, merge_daily, window_total
from app.mapping.inputs import RepoData, UserData
from app.mapping.patterns import BARS, GENERATORS, STEPS_PER_BAR, PatternContext
from app.mapping.rng import SeededRng, stable_hash
from app.mapping.scaling import log_scale, rounded
from app.mapping.theory import NOTE_NAMES, PROGRESSIONS, SCALE_NAMES
from app.mapping.version import MAPPING_VERSION

RHYTHM_DAYS = 365  # Regularity is judged over the last year.
RECENT_DAYS = 90  # Intensity comes from the last three months.
CONTRIBUTIONS_FOR_FULL_INTENSITY = 150
MIN_BPM, MAX_BPM = 76, 124
MAX_TRACKS = 5

# Track roles by language rank: the biggest language carries the melody, and so on.
ROLES = ("melody", "bass", "chords", "arpeggio", "drums")
# Instruments the frontend knows, per role. A language always gets the same one for a role.
INSTRUMENTS: dict[str, tuple[str, ...]] = {
    "melody": ("piano", "pluck", "bell", "lead"),
    "bass": ("synth-bass", "fm-bass"),
    "chords": ("pad", "strings", "organ"),
    "arpeggio": ("marimba", "pluck", "bell"),
    "drums": ("drum-kit",),
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True)


class Track(_Frozen):
    language: str
    role: str
    instrument: str
    # 0..1 playback gain, from the language's share of all code bytes.
    volume: float
    # The language's share of all code bytes (0..1).
    share: float
    # BARS * STEPS_PER_BAR steps; each lists the notes (or drum hits) played on it.
    steps: tuple[tuple[str, ...], ...]


class MusicParams(_Frozen):
    version: int
    username: str
    as_of: dt.date
    tempo_bpm: int
    # 0 (steady) to 1 (bursty): how much the frontend should let timing drift and swing.
    tempo_variation: float
    key: str
    scale: str
    # 0..1, from recent activity; also controls how many notes the patterns contain.
    intensity: float
    bars: int
    steps_per_bar: int
    tracks: tuple[Track, ...]


def build_music(user: UserData, *, as_of: dt.date) -> MusicParams:
    """Derive the music. Pure: the same user and as_of always give identical parameters."""
    # The contributions calendar is the user's own rhythm; repo commits also include other
    # people's work, so they're only a fallback for users with an empty calendar.
    rhythm = user.contributions or merge_daily(repo.daily_commits for repo in user.repos)
    variation = (burstiness(daily_series(rhythm, end=as_of, days=RHYTHM_DAYS)) + 1) / 2
    intensity = log_scale(
        window_total(rhythm, end=as_of, days=RECENT_DAYS),
        saturation=CONTRIBUTIONS_FOR_FULL_INTENSITY,
    )

    key_rng = SeededRng(user.login, "key")
    key = key_rng.below(len(NOTE_NAMES))
    scale = key_rng.pick(SCALE_NAMES)
    context = PatternContext(
        key=key,
        scale=scale,
        progression=SeededRng(user.login, "progression").pick(PROGRESSIONS),
        # Even a quiet profile gets a sparse tune rather than silence.
        density=0.25 + 0.65 * intensity,
        syncopation=variation,
    )

    return MusicParams(
        version=MAPPING_VERSION,
        username=user.login.lower(),
        as_of=as_of,
        # Steady activity: a calm tempo. Bursty activity: faster and more varied.
        tempo_bpm=round(MIN_BPM + (MAX_BPM - MIN_BPM) * variation),
        tempo_variation=rounded(variation),
        key=NOTE_NAMES[key],
        scale=scale,
        intensity=rounded(intensity),
        bars=BARS,
        steps_per_bar=STEPS_PER_BAR,
        tracks=tuple(_tracks(user, context)),
    )


def _tracks(user: UserData, context: PatternContext) -> list[Track]:
    totals = language_totals(user.repos)
    all_bytes = sum(totals.values())
    if all_bytes == 0:
        return []
    top = sorted(totals.items(), key=lambda item: (-item[1], item[0]))[:MAX_TRACKS]
    tracks = []
    for role, (language, size) in zip(ROLES, top, strict=False):
        share = size / all_bytes
        options = INSTRUMENTS[role]
        rng = SeededRng(user.login, f"pattern:{role}:{language}")
        tracks.append(
            Track(
                language=language,
                role=role,
                instrument=options[stable_hash(language) % len(options)],
                # Square root so a 4% language is still clearly audible next to a 90% one.
                volume=rounded(0.35 + 0.65 * math.sqrt(share)),
                share=rounded(share),
                steps=GENERATORS[role](rng, context),
            )
        )
    return tracks


def language_totals(repos: Sequence[RepoData]) -> dict[str, int]:
    """Bytes of code per language across all repos."""
    totals: dict[str, int] = {}
    for repo in repos:
        for language, size in repo.languages.items():
            totals[language] = totals.get(language, 0) + size
    return totals
