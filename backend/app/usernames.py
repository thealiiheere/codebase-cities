"""GitHub username rules, shared by the API (validation) and the GitHub client (safety)."""

import re

LOGIN_MAX_LENGTH = 39
# Runs of letters and digits joined by single hyphens: no leading, trailing, or double hyphens.
# Written without lookaheads so Pydantic's regex engine can use it for path validation too.
LOGIN_REGEX = r"^[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*$"
LOGIN_PATTERN = re.compile(LOGIN_REGEX)


def is_valid_login(login: str) -> bool:
    return len(login) <= LOGIN_MAX_LENGTH and LOGIN_PATTERN.fullmatch(login) is not None


def normalize_login(login: str) -> str:
    """GitHub logins are case-insensitive; we store and compare them lowercase."""
    return login.lower()
