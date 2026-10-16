"""Placing rectangles (districts) around a reserved center (the plaza) without overlaps."""

import math
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

# Tolerance for float rounding, so two rectangles exactly `gap` apart don't count as touching.
_EPSILON = 1e-6


@dataclass(frozen=True)
class Rect:
    """An axis-aligned rectangle on the ground plane (x to the east, z to the south)."""

    x0: float
    z0: float
    x1: float
    z1: float

    @classmethod
    def centered(cls, x: float, z: float, width: float, depth: float) -> "Rect":
        return cls(x - width / 2, z - depth / 2, x + width / 2, z + depth / 2)

    @property
    def center(self) -> tuple[float, float]:
        return (self.x0 + self.x1) / 2, (self.z0 + self.z1) / 2

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def depth(self) -> float:
        return self.z1 - self.z0

    def distance_to(self, other: "Rect") -> float:
        """How far apart two non-overlapping rectangles are along the axis that separates them."""
        return max(other.x0 - self.x1, self.x0 - other.x1, other.z0 - self.z1, self.z0 - other.z1)

    def overlaps(self, other: "Rect", gap: float = 0.0) -> bool:
        """True if the rectangles overlap or come closer than `gap`."""
        reach = gap - _EPSILON
        return (
            self.x0 < other.x1 + reach
            and other.x0 < self.x1 + reach
            and self.z0 < other.z1 + reach
            and other.z0 < self.z1 + reach
        )


def place_around(sizes: Sequence[tuple[float, float]], *, center: Rect, gap: float) -> list[Rect]:
    """Place rectangles of the given (width, depth), in order, as near the center as possible.

    Each one goes flush against a side of an already placed rectangle (or the center), at the
    valid spot nearest the center. A rectangle is never placed nearer the center than the one
    before it, so with the biggest first, distance from the center follows size; gaps left
    behind stay open. Every result keeps at least `gap` from the center and from each other.
    """
    placed: list[Rect] = []
    nearest_allowed = 0.0
    for width, depth in sizes:
        blockers = [center, *placed]
        candidates = (
            rect
            for blocker in blockers
            for rect in _touching(blocker, width, depth, gap)
            if rect.distance_to(center) >= nearest_allowed - _EPSILON
            and not any(rect.overlaps(other, gap) for other in blockers)
        )
        # A spot always exists: flush against the outside of the outermost rectangle.
        best = min(candidates, key=lambda rect: _nearest_first(rect, center))
        placed.append(best)
        nearest_allowed = best.distance_to(center)
    return placed


def _touching(blocker: Rect, width: float, depth: float, gap: float) -> Iterator[Rect]:
    """Spots for a width x depth rectangle exactly `gap` away from one side of `blocker`,
    aligned to that side's start, middle, or end."""
    bx, bz = blocker.center
    east, west = blocker.x1 + gap + width / 2, blocker.x0 - gap - width / 2
    south, north = blocker.z1 + gap + depth / 2, blocker.z0 - gap - depth / 2
    along_z = (blocker.z0 + depth / 2, bz, blocker.z1 - depth / 2)
    along_x = (blocker.x0 + width / 2, bx, blocker.x1 - width / 2)
    for z in along_z:
        yield Rect.centered(east, z, width, depth)
        yield Rect.centered(west, z, width, depth)
    for x in along_x:
        yield Rect.centered(x, south, width, depth)
        yield Rect.centered(x, north, width, depth)


def _nearest_first(rect: Rect, center: Rect) -> tuple[float, float, float, float, float]:
    x, z = rect.center
    # Gap to the center first, then how far the rectangle's middle is; the angle and the
    # coordinates break ties so the choice never depends on candidate order.
    return (
        round(rect.distance_to(center), 6),
        round(x * x + z * z, 6),
        round(math.atan2(z, x), 6),
        x,
        z,
    )
