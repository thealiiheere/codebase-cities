"""Application settings, loaded from environment variables and an optional `.env` file."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# The `.env` file lives at the repository root, shared with docker-compose. Resolving it from this
# file (rather than the working directory) means `uv run` works from any folder. Inside Docker the
# file does not exist and compose passes the variables in directly.
_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    environment: Literal["development", "test", "production"] = "development"

    database_url: str = "postgresql+asyncpg://cities:cities@localhost:5432/cities"
    redis_url: str = "redis://localhost:6379/0"

    # Optional so the API can boot (e.g. for health checks) without it; the GitHub client
    # refuses to make requests when it is missing.
    github_token: SecretStr | None = None

    # Cap on repos ingested per user, most recently pushed first.
    max_repos_per_user: int = Field(default=100, ge=1, le=1000)

    github_api_url: str = "https://api.github.com"
    # Newest commits fetched per repo. Older ones still count toward the repo's total.
    github_max_commits_per_repo: int = Field(default=5000, ge=100)
    # Start waiting (or give up) when this little quota is left, keeping some in reserve.
    github_rate_limit_reserve: int = Field(default=50, ge=0)
    # Longest we will sleep for a rate limit reset before failing with a clear error instead.
    github_max_wait_seconds: float = Field(default=60, ge=0)
    github_max_retries: int = Field(default=3, ge=0, le=10)
    github_timeout_seconds: float = Field(default=30, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
