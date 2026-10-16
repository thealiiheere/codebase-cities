"""The building blocks: randomness, scaling, activity measures, colors, theory, packing."""

import datetime as dt
import itertools
import re

import pytest

from app.mapping.activity import burstiness, daily_series, merge_daily, window_total
from app.mapping.packing import Rect, place_around
from app.mapping.palette import LANGUAGE_COLORS, OTHER, language_color
from app.mapping.rng import SeededRng, stable_hash
from app.mapping.scaling import clamp, log_scale, rounded
from app.mapping.theory import note_name, triad

D = dt.date


# --- rng ---------------------------------------------------------------------------------------


def test_stable_hash_never_changes() -> None:
    # Pinned values: if these change, every city and tune changes with them.
    assert stable_hash("octocat") == 0xA6658157F0DF8390
    assert stable_hash("octocat") != stable_hash("octocat2")


def test_seeded_stream_never_changes() -> None:
    # Python promises random() keeps this sequence for a given seed across versions.
    rng = SeededRng("octocat", "music")
    assert [rng.random(), rng.random()] == [0.19323682347026117, 0.8011723850254504]


def test_same_username_and_purpose_give_the_same_stream() -> None:
    a, b = SeededRng("octocat", "music"), SeededRng("OctoCat", "music")
    assert [a.random() for _ in range(5)] == [b.random() for _ in range(5)]


def test_purposes_get_independent_streams() -> None:
    assert SeededRng("octocat", "music").random() != SeededRng("octocat", "styles").random()


def test_rng_helpers_stay_in_range() -> None:
    rng = SeededRng("octocat", "test")
    assert all(0 <= rng.below(7) < 7 for _ in range(500))
    assert all(2 <= rng.uniform(2, 3) < 3 for _ in range(500))
    assert {rng.pick("abc") for _ in range(200)} == {"a", "b", "c"}


# --- scaling -----------------------------------------------------------------------------------


def test_clamp() -> None:
    assert (clamp(-1, 0, 1), clamp(0.5, 0, 1), clamp(9, 0, 1)) == (0, 0.5, 1)


def test_log_scale_endpoints_and_saturation() -> None:
    assert log_scale(0, saturation=100, low=3, high=10) == 3
    assert log_scale(100, saturation=100, low=3, high=10) == 10
    assert log_scale(10**9, saturation=100, low=3, high=10) == 10
    assert log_scale(-5, saturation=100, low=3, high=10) == 3


def test_log_scale_is_monotonic_and_compresses() -> None:
    values = [log_scale(v, saturation=10_000) for v in (1, 10, 100, 1000, 10_000)]
    assert values == sorted(values)
    # On a log curve each tenfold step adds about the same amount.
    steps = [b - a for a, b in itertools.pairwise(values)]
    assert max(steps) - min(steps) < 0.1


def test_rounded() -> None:
    assert rounded(1.23456) == 1.23


# --- activity ----------------------------------------------------------------------------------


def test_window_total_is_inclusive_and_ignores_the_future() -> None:
    daily = {D(2026, 1, 10): 1, D(2026, 1, 1): 2, D(2025, 12, 31): 4, D(2026, 1, 11): 8}
    assert window_total(daily, end=D(2026, 1, 10), days=10) == 3


def test_daily_series_fills_quiet_days_with_zero() -> None:
    assert daily_series({D(2026, 1, 2): 5}, end=D(2026, 1, 3), days=3) == [0, 5, 0]


@pytest.mark.parametrize(
    ("series", "expected"),
    [([3] * 30, -1.0), ([0] * 30, -1.0), ([], -1.0), ([30] + [0] * 29, 0.69)],
)
def test_burstiness(series: list[int], expected: float) -> None:
    assert burstiness(series) == pytest.approx(expected, abs=0.01)


def test_weekday_rhythm_is_steadier_than_monthly_bursts() -> None:
    weekdays = [1, 1, 1, 1, 1, 0, 0] * 8
    monthly = ([20] + [0] * 27) * 2
    assert burstiness(weekdays) < burstiness(monthly)


def test_merge_daily() -> None:
    merged = merge_daily([{D(2026, 1, 1): 1}, {D(2026, 1, 1): 2, D(2026, 1, 2): 3}])
    assert merged == {D(2026, 1, 1): 3, D(2026, 1, 2): 3}


# --- palette -----------------------------------------------------------------------------------

HEX = re.compile(r"^#[0-9a-f]{6}$")


def test_known_languages_use_linguist_colors() -> None:
    assert language_color("Python") == "#3572a5"
    assert language_color(OTHER) == LANGUAGE_COLORS[OTHER]


def test_unknown_languages_get_a_stable_valid_color() -> None:
    color = language_color("Brainfork")
    assert HEX.match(color)
    assert color == language_color("Brainfork")
    assert color != language_color("Brainfork2")


def test_every_listed_color_is_valid_hex() -> None:
    assert all(HEX.match(color) for color in LANGUAGE_COLORS.values())


# --- theory ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("key", "scale", "degree", "octave", "expected"),
    [
        (0, "major", 0, 4, "C4"),
        (0, "major", 4, 4, "G4"),
        (0, "major", 7, 4, "C5"),  # Past the scale's end: next octave.
        (0, "major", -1, 4, "B3"),  # Below the root: previous octave.
        (9, "minor", 2, 3, "C4"),  # A minor's third crosses into the next octave.
        (0, "minor-pentatonic", 5, 4, "C5"),
    ],
)
def test_note_name(key: int, scale: str, degree: int, octave: int, expected: str) -> None:
    assert note_name(key, scale, degree, octave) == expected


def test_triad() -> None:
    assert triad(0, "major", 0, 4) == ("C4", "E4", "G4")
    assert triad(0, "major", 5, 4) == ("A4", "C5", "E5")


# --- packing -----------------------------------------------------------------------------------


def test_rect_overlap_respects_the_gap() -> None:
    a = Rect(0, 0, 10, 10)
    assert a.overlaps(Rect(5, 5, 15, 15))
    assert not a.overlaps(Rect(12, 0, 20, 10), gap=2)  # Exactly 2 apart: allowed.
    assert a.overlaps(Rect(11.9, 0, 20, 10), gap=2)


def test_place_around_never_overlaps_and_keeps_the_gap() -> None:
    center = Rect.centered(0, 0, 30, 30)
    sizes = [(40, 20), (35, 35), (20, 50), (10, 10), (60, 15), (25, 25), (12, 30)]

    placed = place_around(sizes, center=center, gap=10)

    rects = [center, *placed]
    for i, a in enumerate(rects):
        for b in rects[i + 1 :]:
            assert not a.overlaps(b, gap=10)
    assert [(round(r.width), round(r.depth)) for r in placed] == sizes


def test_place_around_never_puts_a_later_rect_nearer_the_center() -> None:
    center = Rect.centered(0, 0, 30, 30)
    # Big first, then small ones that would fit in gaps next to the center if allowed.
    sizes = [(60, 40), (50, 50), (40, 60), (55, 30), (8, 8), (8, 8), (8, 8), (8, 8)]

    placed = place_around(sizes, center=center, gap=10)

    gaps = [round(rect.distance_to(center), 6) for rect in placed]
    assert gaps == sorted(gaps)
    assert gaps[0] == 10  # The first one sits right across the gap from the center.


def test_rect_distance() -> None:
    assert Rect(0, 0, 10, 10).distance_to(Rect(13, 2, 20, 5)) == 3
    assert Rect(0, 0, 10, 10).distance_to(Rect(-8, 14, -1, 20)) == 4
