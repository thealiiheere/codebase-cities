"""Tracks GitHub rate limits from response headers and decides when to wait or give up.

GitHub sends `x-ratelimit-*` headers on every REST and GraphQL response, so one code path covers
both. Each API family ("core" for REST, "graphql") has its own budget, tracked separately.
"""

import datetime as dt
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.github.errors import RateLimitExceededError

Clock = Callable[[], dt.datetime]

# GitHub's guidance for a secondary limit without a Retry-After header: wait at least a minute.
DEFAULT_SECONDARY_WAIT_SECONDS = 60.0


@dataclass(frozen=True)
class RateLimitState:
    resource: str
    remaining: int
    reset_at: dt.datetime


def parse_rate_limit(headers: Mapping[str, str]) -> RateLimitState | None:
    try:
        remaining = int(headers["x-ratelimit-remaining"])
        reset_epoch = int(headers["x-ratelimit-reset"])
    except (KeyError, ValueError):
        return None
    return RateLimitState(
        resource=headers.get("x-ratelimit-resource", "core"),
        remaining=remaining,
        reset_at=dt.datetime.fromtimestamp(reset_epoch, dt.UTC),
    )


class RateLimiter:
    def __init__(self, *, reserve: int, max_wait_seconds: float, clock: Clock) -> None:
        self._reserve = reserve
        self._max_wait = max_wait_seconds
        self._clock = clock
        self._states: dict[str, RateLimitState] = {}

    @property
    def states(self) -> dict[str, RateLimitState]:
        return dict(self._states)

    def record(self, headers: Mapping[str, str]) -> None:
        state = parse_rate_limit(headers)
        if state is not None:
            self._states[state.resource] = state

    def delay_before_request(self, resource: str) -> float:
        """Seconds to pause before the next request, so we never drain the budget to zero.

        Raises RateLimitExceededError when the reset is too far away to wait for.
        """
        state = self._states.get(resource)
        if state is None or state.remaining > self._reserve:
            return 0.0
        return self._delay_until(state.reset_at, resource)

    def delay_after_limited(
        self, status: int, headers: Mapping[str, str], body: str
    ) -> float | None:
        """For a 403/429 response, seconds to wait before retrying.

        Returns None when the response is not a rate limit (e.g. a permissions 403), so the caller
        treats it as an ordinary error. Raises RateLimitExceededError if the wait is too long.
        """
        resource = headers.get("x-ratelimit-resource", "core")
        retry_after = headers.get("retry-after")
        if retry_after is not None and retry_after.isdigit():
            return self._delay_for(float(retry_after), resource)

        state = parse_rate_limit(headers)
        if state is not None and state.remaining == 0:
            return self._delay_until(state.reset_at, resource)

        if status == 429 or "rate limit" in body.lower():
            return self._delay_for(DEFAULT_SECONDARY_WAIT_SECONDS, resource)
        return None

    def _delay_until(self, reset_at: dt.datetime, resource: str) -> float:
        delay = max((reset_at - self._clock()).total_seconds(), 0.0)
        if delay > self._max_wait:
            raise RateLimitExceededError(resource, reset_at)
        return delay

    def _delay_for(self, seconds: float, resource: str) -> float:
        if seconds > self._max_wait:
            raise RateLimitExceededError(resource, self._clock() + dt.timedelta(seconds=seconds))
        return seconds
