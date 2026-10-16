"""HTTP layer for GitHub: auth headers, retries, rate limits, and ETags.

It knows how to talk to GitHub but nothing about what we ask for; see client.py for that.
"""

import asyncio
import datetime as dt
import logging
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Self
from urllib.parse import urlencode

import httpx
from pydantic import BaseModel

from app.github.errors import (
    GitHubApiError,
    GitHubAuthError,
    GitHubNotFoundError,
    RateLimitExceededError,
)
from app.github.etag import CachedResponse, EtagStore, InMemoryEtagStore
from app.github.ratelimit import Clock, RateLimiter, RateLimitState

logger = logging.getLogger(__name__)

Sleep = Callable[[float], Awaitable[None]]

API_VERSION = "2022-11-28"
RETRYABLE_STATUSES = frozenset({500, 502, 503, 504})
BACKOFF_BASE_SECONDS = 1.0
BACKOFF_CAP_SECONDS = 30.0


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class GraphQLError(BaseModel):
    message: str
    type: str | None = None
    path: list[str | int] = []


@dataclass(frozen=True)
class GraphQLResult:
    data: dict[str, Any]
    errors: list[GraphQLError]


class GitHubTransport:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = "https://api.github.com",
        etag_store: EtagStore | None = None,
        max_retries: int = 3,
        rate_limit_reserve: int = 50,
        max_wait_seconds: float = 60.0,
        timeout_seconds: float = 30.0,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = _utcnow,
    ) -> None:
        if not token:
            raise GitHubAuthError("GITHUB_TOKEN is not set.")
        self._http = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout_seconds,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "codebase-cities",
            },
        )
        self._etags = etag_store if etag_store is not None else InMemoryEtagStore()
        self._max_retries = max_retries
        self._limiter = RateLimiter(
            reserve=rate_limit_reserve, max_wait_seconds=max_wait_seconds, clock=clock
        )
        self._sleep = sleep
        self._clock = clock

    @property
    def rate_limits(self) -> dict[str, RateLimitState]:
        return self._limiter.states

    async def graphql(self, query: str, variables: Mapping[str, Any]) -> GraphQLResult:
        response = await self._send(
            "POST", "/graphql", resource="graphql", json={"query": query, "variables": variables}
        )
        _raise_for_status(response)
        body = response.json()
        errors = [GraphQLError.model_validate(error) for error in body.get("errors") or []]
        # GraphQL can report an exhausted budget as a 200 with an error instead of a 403.
        if any(error.type == "RATE_LIMITED" for error in errors):
            state = self._limiter.states.get("graphql")
            raise RateLimitExceededError("graphql", state.reset_at if state else None)
        return GraphQLResult(data=body.get("data") or {}, errors=errors)

    async def rest_get(self, path: str, params: Mapping[str, str | int] | None = None) -> Any:
        """GET a REST resource, using a stored ETag so unchanged responses cost no quota."""
        key = path + ("?" + urlencode(sorted(params.items())) if params else "")
        cached = await self._etags.get(key)
        headers = {"If-None-Match": cached.etag} if cached else {}

        response = await self._send("GET", path, resource="core", params=params, headers=headers)
        if response.status_code == 304 and cached is not None:
            return cached.body
        _raise_for_status(response)

        body = response.json()
        etag = response.headers.get("etag")
        if etag:
            await self._etags.set(key, CachedResponse(etag=etag, body=body))
        return body

    async def _send(
        self, method: str, path: str, *, resource: str, **kwargs: Any
    ) -> httpx.Response:
        """Send a request, retrying network errors, 5xx responses, and rate limits."""
        for attempt in range(self._max_retries + 1):
            is_last_attempt = attempt == self._max_retries
            await self._pause(self._limiter.delay_before_request(resource), "low rate limit")

            try:
                response = await self._http.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                if is_last_attempt:
                    raise GitHubApiError(f"Could not reach GitHub: {exc!r}") from exc
                await self._pause(_backoff(attempt), "network error")
                continue

            self._limiter.record(response.headers)

            if response.status_code in (403, 429):
                delay = self._limiter.delay_after_limited(
                    response.status_code, response.headers, response.text
                )
                if delay is None:
                    return response  # An ordinary 403, e.g. missing permissions.
                if is_last_attempt:
                    reset_at = self._clock() + dt.timedelta(seconds=delay)
                    raise RateLimitExceededError(resource, reset_at)
                await self._pause(delay, "rate limited")
            elif response.status_code in RETRYABLE_STATUSES and not is_last_attempt:
                await self._pause(_backoff(attempt), f"GitHub returned {response.status_code}")
            else:
                return response

        raise AssertionError("unreachable: the last attempt always returns or raises")

    async def _pause(self, seconds: float, reason: str) -> None:
        if seconds > 0:
            logger.info("Waiting %.1fs before calling GitHub (%s)", seconds, reason)
            await self._sleep(seconds)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()


def _backoff(attempt: int) -> float:
    # Exponential backoff (1s, 2s, 4s, ...) plus jitter, so many workers retrying at once
    # don't all hit GitHub in the same instant.
    exponential = min(BACKOFF_BASE_SECONDS * 2.0**attempt, BACKOFF_CAP_SECONDS)
    return exponential + random.uniform(0, BACKOFF_BASE_SECONDS)


def _raise_for_status(response: httpx.Response) -> None:
    status = response.status_code
    if status < 400:
        return
    if status == 401:
        raise GitHubAuthError("GitHub rejected GITHUB_TOKEN (401). It may be invalid or expired.")
    message = _error_message(response)
    if status == 404:
        raise GitHubNotFoundError(message)
    raise GitHubApiError(f"GitHub returned {status}: {message}", status)


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(body, dict) and "message" in body:
        return str(body["message"])
    return response.text[:200]
