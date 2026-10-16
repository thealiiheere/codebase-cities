"""Errors raised by the GitHub client. Higher layers map them to HTTP responses."""

import datetime as dt


class GitHubError(Exception):
    """Base class for every GitHub client error."""


class GitHubAuthError(GitHubError):
    """GITHUB_TOKEN is missing, invalid, or expired."""


class GitHubNotFoundError(GitHubError):
    """A REST resource does not exist."""


class GitHubUserNotFoundError(GitHubNotFoundError):
    def __init__(self, login: str, reason: str = "does not exist") -> None:
        super().__init__(f"GitHub user '{login}' {reason}.")
        self.login = login


class RateLimitExceededError(GitHubError):
    def __init__(self, resource: str, reset_at: dt.datetime | None) -> None:
        when = f" It resets at {reset_at:%Y-%m-%d %H:%M} UTC." if reset_at else ""
        super().__init__(f"GitHub {resource} rate limit exhausted.{when}")
        self.resource = resource
        self.reset_at = reset_at


class GitHubApiError(GitHubError):
    """Any other failure: unexpected status, GraphQL error, or GitHub unreachable after retries."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
