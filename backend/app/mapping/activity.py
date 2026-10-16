"""Measuring activity in daily counts: totals over a window, and how bursty it is."""

import datetime as dt
import math
from collections.abc import Iterable, Mapping

DailyCounts = Mapping[dt.date, int]


def window_total(daily: DailyCounts, *, end: dt.date, days: int) -> int:
    """Sum of counts in the `days` days ending on `end` (inclusive)."""
    start = end - dt.timedelta(days=days - 1)
    return sum(count for date, count in daily.items() if start <= date <= end)


def daily_series(daily: DailyCounts, *, end: dt.date, days: int) -> list[int]:
    """The counts for each of the `days` days ending on `end`, oldest first, zeros included."""
    start = end - dt.timedelta(days=days - 1)
    return [daily.get(start + dt.timedelta(days=offset), 0) for offset in range(days)]


def burstiness(series: Iterable[int]) -> float:
    """How bursty a series is, from -1 (perfectly steady) to 1 (everything in rare spikes).

    This is the Goh-Barabasi measure (sigma - mu) / (sigma + mu). A user committing the same
    amount every day scores -1; one who commits 30 times on one day a month scores about 0.7.
    No activity at all counts as steady.
    """
    values = list(series)
    if not values:
        return -1.0
    mean = sum(values) / len(values)
    deviation = math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))
    if mean + deviation == 0:
        return -1.0
    return (deviation - mean) / (deviation + mean)


def merge_daily(series: Iterable[DailyCounts]) -> dict[dt.date, int]:
    """Add several daily-count maps together."""
    total: dict[dt.date, int] = {}
    for daily in series:
        for date, count in daily.items():
            total[date] = total.get(date, 0) + count
    return total
