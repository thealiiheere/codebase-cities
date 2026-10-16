"""Small numeric helpers for turning counts into visual and musical ranges."""

import math


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def log_scale(value: float, *, saturation: float, low: float = 0.0, high: float = 1.0) -> float:
    """Map 0..saturation onto low..high on a log curve; values past saturation stay at high.

    A log curve keeps small numbers distinguishable (1 vs 10 commits) without letting huge
    ones (50,000 commits) dwarf everything else.
    """
    fraction = math.log1p(max(value, 0)) / math.log1p(saturation)
    return low + (high - low) * clamp(fraction, 0.0, 1.0)


def rounded(value: float) -> float:
    """Round output floats to 2 decimals: plenty for rendering, and it keeps the JSON stable
    even if two machines' math libraries disagree in the last binary digit."""
    return round(value, 2)
