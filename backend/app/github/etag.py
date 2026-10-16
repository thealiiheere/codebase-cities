"""Storage for REST responses and their ETags, so repeat requests can be conditional.

When we send `If-None-Match: <etag>` and nothing changed, GitHub replies 304 with no body and
does not charge the request against the rate limit. We then serve the body we stored earlier.
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class CachedResponse:
    etag: str
    body: Any


class EtagStore(Protocol):
    async def get(self, key: str) -> CachedResponse | None: ...

    async def set(self, key: str, value: CachedResponse) -> None: ...


class InMemoryEtagStore:
    """Per-process store. Fine for tests and scripts; workers will share a Redis-backed one."""

    def __init__(self) -> None:
        self._entries: dict[str, CachedResponse] = {}

    async def get(self, key: str) -> CachedResponse | None:
        return self._entries.get(key)

    async def set(self, key: str, value: CachedResponse) -> None:
        self._entries[key] = value
