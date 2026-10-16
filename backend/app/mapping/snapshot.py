"""The timeline: what a user's data looked like on a past date."""

import datetime as dt

from app.mapping.inputs import RepoData, UserData


def snapshot_at(user: UserData, until: dt.date) -> UserData:
    """The user's data as of the end of `until` (UTC).

    Repos created after that day are left out, and commits and contributions after it are
    dropped. GitHub doesn't offer star, fork, size, or language history cheaply, so those keep
    their current values: a timeline shows the city growing, not its stars changing.
    """
    return user.model_copy(
        update={
            "repos": tuple(
                _repo_at(repo, until) for repo in user.repos if repo.created_at.date() <= until
            ),
            "contributions": {
                date: count for date, count in user.contributions.items() if date <= until
            },
        }
    )


def _repo_at(repo: RepoData, until: dt.date) -> RepoData:
    later = sum(count for date, count in repo.daily_commits.items() if date > until)
    pushed_at = repo.pushed_at if repo.pushed_at and repo.pushed_at.date() <= until else None
    return repo.model_copy(
        update={
            # Fetched history can be capped (newest commits only), so we can't count commits up
            # to `until` directly. Subtracting the known later commits from the true total is
            # exact whenever `until` falls inside the fetched window, and an overestimate before
            # it, which the log-scaled building heights hide well.
            "total_commits": max(repo.total_commits - later, 0),
            "daily_commits": {
                date: count for date, count in repo.daily_commits.items() if date <= until
            },
            # We only know the latest push, so before it we can't tell when the previous one was.
            "pushed_at": pushed_at,
        }
    )
