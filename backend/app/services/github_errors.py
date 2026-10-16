"""Turning GitHub client errors into API errors and user-facing job messages."""

import datetime as dt
import logging
import math
from collections.abc import Iterator
from contextlib import contextmanager

from app.errors import GitHubRateLimitedError, GitHubUnavailableError, UnknownGitHubUserError
from app.github.errors import GitHubError, GitHubUserNotFoundError, RateLimitExceededError

logger = logging.getLogger(__name__)

GITHUB_UNAVAILABLE_MESSAGE = "GitHub is unavailable right now. Please try again later."


def failure_message(exc: GitHubError) -> str:
    """What a user should read when a job fails because of GitHub."""
    if isinstance(exc, GitHubUserNotFoundError | RateLimitExceededError):
        return str(exc)
    # Anything else (bad token, outage) is our problem, not theirs: log details, show a summary.
    logger.error("GitHub error: %s", exc)
    return GITHUB_UNAVAILABLE_MESSAGE


@contextmanager
def github_errors_as_app_errors(now: dt.datetime) -> Iterator[None]:
    """Re-raise GitHub errors as AppErrors, which the API renders as 404 or 503."""
    try:
        yield
    except GitHubUserNotFoundError as exc:
        raise UnknownGitHubUserError(str(exc)) from exc
    except RateLimitExceededError as exc:
        headers = None
        if exc.reset_at is not None:
            # Retry-After tells clients (and the frontend) exactly how long to wait.
            seconds = max(math.ceil((exc.reset_at - now).total_seconds()), 1)
            headers = {"Retry-After": str(seconds)}
        raise GitHubRateLimitedError(str(exc), headers=headers) from exc
    except GitHubError as exc:
        raise GitHubUnavailableError(failure_message(exc)) from exc
