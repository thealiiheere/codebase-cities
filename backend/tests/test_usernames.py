import pytest

from app.usernames import is_valid_login, normalize_login


@pytest.mark.parametrize("login", ["octocat", "Octo-Cat", "a", "a1-b2-c3", "x" * 39])
def test_accepts_github_style_logins(login: str) -> None:
    assert is_valid_login(login)


@pytest.mark.parametrize(
    "login", ["", "-lead", "trail-", "dou--ble", "under_score", "../etc", "a b", "x" * 40]
)
def test_rejects_invalid_logins(login: str) -> None:
    assert not is_valid_login(login)


def test_normalizes_to_lowercase() -> None:
    assert normalize_login("OctoCat") == "octocat"
