import datetime as dt

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.github.models import ContributionDay
from app.ingestion.fetch import FetchedUserData
from app.ingestion.store import store_user_data
from app.models import Base, DailyCommit, DailyContribution, GithubUser, Repo, RepoLanguage
from tests.fakes import T0, make_history, make_profile, make_repo

pytestmark = pytest.mark.integration

UTC = dt.UTC
DAY1 = dt.datetime(2026, 3, 1, 10, tzinfo=UTC)
DAY2 = dt.datetime(2026, 3, 2, 10, tzinfo=UTC)


def octocat_data(**overrides: object) -> FetchedUserData:
    fields: dict[str, object] = {
        "profile": make_profile(),
        "repos": [make_repo("city", 1001), make_repo("music", 1002, languages={"Rust": 50})],
        "calendar": [
            ContributionDay(date=dt.date(2026, 3, 1), count=2),
            ContributionDay(date=dt.date(2026, 3, 2), count=0),
        ],
        "histories": {
            "city": make_history("city", DAY1, DAY1, DAY2),
            "music": make_history("music", DAY2),
        },
    }
    fields.update(overrides)
    return FetchedUserData(**fields)  # type: ignore[arg-type]


async def store(session_factory: async_sessionmaker[AsyncSession], data: FetchedUserData) -> int:
    async with session_factory() as session, session.begin():
        return await store_user_data(session, data, fetched_at=T0)


async def count(session: AsyncSession, model: type[Base]) -> int:
    return await session.scalar(select(func.count()).select_from(model)) or 0


async def test_stores_everything(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    user_id = await store(session_factory, octocat_data())

    user = await session.get(GithubUser, user_id)
    assert user is not None
    assert (user.login, user.github_id, user.fetched_at) == ("octocat", 583231, T0)

    repos = {repo.name: repo for repo in await session.scalars(select(Repo))}
    assert set(repos) == {"city", "music"}
    assert repos["city"].total_commits == 3
    assert repos["city"].user_id == user_id

    commits = await session.execute(
        select(Repo.name, DailyCommit.date, DailyCommit.count).join(Repo).order_by(Repo.name)
    )
    assert commits.all() == [
        ("city", dt.date(2026, 3, 1), 2),
        ("city", dt.date(2026, 3, 2), 1),
        ("music", dt.date(2026, 3, 2), 1),
    ]

    languages = await session.execute(
        select(Repo.name, RepoLanguage.language, RepoLanguage.bytes)
        .join(Repo)
        .order_by(Repo.name, RepoLanguage.language)
    )
    assert languages.all() == [
        ("city", "Python", 9000),
        ("city", "Shell", 1000),
        ("music", "Rust", 50),
    ]

    contributions = await session.execute(select(DailyContribution.date, DailyContribution.count))
    assert contributions.all() == [(dt.date(2026, 3, 1), 2)]  # Zero days are not stored.


async def test_restoring_replaces_old_data(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    first_id = await store(session_factory, octocat_data())
    city_id = await session.scalar(select(Repo.id).where(Repo.name == "city"))

    # "music" is gone, "city" was renamed and gained a language, history changed.
    updated = octocat_data(
        repos=[make_repo("city-v2", 1001, languages={"Go": 7}, stars=99)],
        histories={"city-v2": make_history("city-v2", DAY2)},
        calendar=[],
    )
    second_id = await store(session_factory, updated)

    assert second_id == first_id
    repo = await session.scalar(select(Repo))
    assert repo is not None
    assert (repo.id, repo.name, repo.stars) == (city_id, "city-v2", 99)
    assert await count(session, Repo) == 1
    assert (await session.execute(select(RepoLanguage.language))).scalars().all() == ["Go"]
    assert (await session.execute(select(DailyCommit.date, DailyCommit.count))).all() == [
        (dt.date(2026, 3, 2), 1)
    ]
    assert await count(session, DailyContribution) == 0


async def test_user_rename_keeps_the_same_row(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    first_id = await store(session_factory, octocat_data())

    second_id = await store(session_factory, octocat_data(profile=make_profile(login="NewName")))

    assert second_id == first_id
    assert (await session.scalars(select(GithubUser.login))).all() == ["newname"]


async def test_login_reused_by_another_account_replaces_the_stale_user(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    await store(session_factory, octocat_data())
    # A different account (new github_id) now owns the login "octocat".
    newcomer = octocat_data(
        profile=make_profile(github_id=999), repos=[make_repo("fresh", 2001)], histories={}
    )

    await store(session_factory, newcomer)

    users = (await session.execute(select(GithubUser.github_id, GithubUser.login))).all()
    assert users == [(999, "octocat")]
    assert (await session.scalars(select(Repo.name))).all() == ["fresh"]


async def test_repo_transferred_between_users_moves(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    await store(session_factory, octocat_data())
    other = FetchedUserData(
        profile=make_profile(login="hubot", github_id=42),
        repos=[make_repo("city", 1001)],
        calendar=[],
        histories={},
    )

    hubot_id = await store(session_factory, other)

    city = await session.scalar(select(Repo).where(Repo.github_id == 1001))
    assert city is not None
    assert city.user_id == hubot_id


async def test_user_without_repos(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    await store(session_factory, octocat_data(repos=[], histories={}))

    assert await count(session, GithubUser) == 1
    assert await count(session, Repo) == 0
