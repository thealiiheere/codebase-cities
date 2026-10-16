"""Manual check of the GitHub client against the real API. Not part of the test suite.

The automated tests use mocked responses, so they can't catch a misspelled GraphQL field.
Run this once with a real token to confirm the queries work:

    uv run python -m scripts.github_smoke octocat

Reads GITHUB_TOKEN from the repo-root .env. Uses a few dozen rate limit points at most.
"""

import asyncio
import sys

from app.config import get_settings
from app.github.client import create_github_client
from app.github.errors import GitHubError


async def main(login: str) -> None:
    async with create_github_client(get_settings()) as github:
        profile = await github.lookup_user(login)
        print(f"User: {profile.login} ({profile.name}), joined {profile.created_at:%Y-%m-%d}")

        repos = await github.get_repos(login, limit=5)
        print(f"\nMost recently pushed repos ({len(repos)}):")
        for repo in repos:
            print(
                f"  {repo.name}: {repo.total_commits} commits, {repo.stars} stars, "
                f"{repo.size_kb} KB, {repo.primary_language}"
            )

        days = await github.get_contribution_calendar(login)
        active = [day for day in days if day.count]
        total = sum(day.count for day in days)
        print(f"\nCalendar: {len(days)} days, {len(active)} active, {total} contributions")

        histories = await github.get_commit_history(profile.login, [r.name for r in repos[:3]])
        print("\nCommit history (newest commit, fetched / total):")
        for repo in repos[:3]:
            history = histories[repo.name]
            newest = f"{history.committed_at[0]:%Y-%m-%d}" if history.committed_at else "-"
            print(f"  {repo.name}: {newest}, {len(history.committed_at)} / {repo.total_commits}")

        print("\nRate limits left:")
        for state in github.rate_limits.values():
            print(f"  {state.resource}: {state.remaining} (resets {state.reset_at:%H:%M} UTC)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: uv run python -m scripts.github_smoke <github-username>")
    try:
        asyncio.run(main(sys.argv[1]))
    except GitHubError as exc:
        sys.exit(f"GitHub error: {exc}")
