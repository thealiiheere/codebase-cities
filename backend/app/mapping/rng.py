"""Seeded randomness: the only source of randomness the mapping engine may use.

Each (username, purpose) pair gets its own stream, so adding randomness for one feature never
shifts the numbers another feature sees.

Only `random()` from Python's generator is used. Python guarantees that method keeps producing
the same sequence for the same seed across versions; `choice`, `randint`, and `shuffle` carry
no such promise, so the helpers below are built on `random()` alone.
"""

import hashlib
import random
from collections.abc import Sequence
from typing import TypeVar

T = TypeVar("T")


def stable_hash(text: str) -> int:
    """A 64-bit hash that is identical in every process and on every machine.

    (Python's built-in hash() of a string changes each time the interpreter starts.)
    """
    return int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")


class SeededRng:
    def __init__(self, username: str, purpose: str) -> None:
        self._random = random.Random(stable_hash(f"{username.lower()}:{purpose}"))

    def random(self) -> float:
        """A float in [0, 1)."""
        return self._random.random()

    def uniform(self, low: float, high: float) -> float:
        return low + (high - low) * self.random()

    def below(self, n: int) -> int:
        """An int in [0, n)."""
        return min(int(self.random() * n), n - 1)

    def chance(self, probability: float) -> bool:
        return self.random() < probability

    def pick(self, options: Sequence[T]) -> T:
        return options[self.below(len(options))]
