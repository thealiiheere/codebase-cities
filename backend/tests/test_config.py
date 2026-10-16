import pytest
from pydantic import ValidationError

from app.config import Settings


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GITHUB_TOKEN", "MAX_REPOS_PER_USER", "DATABASE_URL", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)


def test_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.max_repos_per_user == 100
    assert settings.github_token is None
    assert settings.database_url.startswith("postgresql+asyncpg://")


@pytest.mark.parametrize(
    "url",
    [
        "postgres://user:pw@dpg-abc-a/cities",  # Render and Heroku style
        "postgresql://user:pw@dpg-abc-a/cities",
        "postgresql+asyncpg://user:pw@dpg-abc-a/cities",  # Already right: unchanged.
    ],
)
def test_database_url_always_uses_asyncpg(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("DATABASE_URL", url)

    assert Settings(_env_file=None).database_url == "postgresql+asyncpg://user:pw@dpg-abc-a/cities"


def test_reads_environment_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_example")
    monkeypatch.setenv("MAX_REPOS_PER_USER", "25")

    settings = Settings(_env_file=None)

    assert settings.max_repos_per_user == 25
    assert settings.github_token is not None
    assert settings.github_token.get_secret_value() == "ghp_example"


def test_token_is_hidden_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_example")

    assert "ghp_example" not in repr(Settings(_env_file=None))


@pytest.mark.parametrize("value", ["0", "-5", "5000"])
def test_rejects_out_of_range_repo_cap(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("MAX_REPOS_PER_USER", value)

    with pytest.raises(ValidationError):
        Settings(_env_file=None)
