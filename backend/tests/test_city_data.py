"""Loading stored rows back into mapping inputs (needs PostgreSQL)."""

import datetime as dt

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.github.models import ContributionDay
from app.ingestion.fetch import FetchedUserData
from app.ingestion.store import store_user_data
from app.services.city_data import load_user_data
from tests.fakes import T0, make_history, make_profile, make_repo

pytestmark = pytest.mark.integration

UTC = dt.UTC


async def test_round_trip_from_fetched_data_to_mapping_inputs(
    session_factory: async_sessionmaker[AsyncSession], session: AsyncSession
) -> None:
    fetched = FetchedUserData(
        profile=make_profile(),
        repos=[
            make_repo("city", 1001, languages={"Python": 900, "Shell": 100}, total_commits=40),
            make_repo("empty", 1002, languages={}, primary_language=None),
        ],
        calendar=[
            ContributionDay(date=dt.date(2026, 3, 1), count=3),
            ContributionDay(date=dt.date(2026, 3, 2), count=0),
        ],
        histories={
            "city": make_history(
                "city",
                dt.datetime(2026, 3, 1, 10, tzinfo=UTC),
                dt.datetime(2026, 3, 1, 12, tzinfo=UTC),
            )
        },
    )
    async with session_factory() as writer, writer.begin():
        await store_user_data(writer, fetched, fetched_at=T0)

    stored = await load_user_data(session, "octocat")

    assert stored is not None
    assert stored.fetched_at == T0
    data = stored.data
    assert data.login == "octocat"
    assert data.contributions == {dt.date(2026, 3, 1): 3}
    repos = {repo.name: repo for repo in data.repos}
    assert set(repos) == {"city", "empty"}
    assert repos["city"].languages == {"Python": 900, "Shell": 100}
    assert repos["city"].daily_commits == {dt.date(2026, 3, 1): 2}
    assert repos["city"].total_commits == 40
    assert repos["empty"].languages == {}
    assert repos["empty"].daily_commits == {}
    assert repos["empty"].primary_language is None


async def test_unknown_login(session: AsyncSession) -> None:
    assert await load_user_data(session, "ghost") is None
