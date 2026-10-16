"""Pure parts of serving a city: the until rules, cache keys, and the payload."""

import datetime as dt

from app.mapping import MAPPING_VERSION
from app.services.city_payload import build_payload, cache_key, effective_until
from tests.mapping_fakes import repo as make_repo
from tests.mapping_fakes import user

FETCHED = dt.datetime(2026, 10, 16, 9, 30, tzinfo=dt.UTC)


def test_until_before_the_fetch_day_is_kept() -> None:
    assert effective_until(dt.date(2026, 10, 15), FETCHED) == dt.date(2026, 10, 15)


def test_until_on_or_after_the_fetch_day_means_latest() -> None:
    assert effective_until(None, FETCHED) is None
    assert effective_until(dt.date(2026, 10, 16), FETCHED) is None
    assert effective_until(dt.date(2030, 1, 1), FETCHED) is None


def test_cache_key_names_version_user_fetch_and_moment() -> None:
    latest = cache_key("octocat", FETCHED, None)
    past = cache_key("octocat", FETCHED, dt.date(2020, 1, 1))
    refetched = cache_key("octocat", FETCHED + dt.timedelta(hours=1), None)

    assert latest == f"city:v{MAPPING_VERSION}:octocat:{int(FETCHED.timestamp())}:latest"
    assert past.endswith(":2020-01-01")
    assert len({latest, past, refetched}) == 3


def test_payload_for_the_latest_city() -> None:
    data = user(make_repo("r", 1))

    payload = build_payload(data, FETCHED, None)

    assert payload.as_of == FETCHED.date()
    assert payload.until is None
    assert payload.timeline.start == data.created_at.date()
    assert payload.timeline.end == FETCHED.date()
    assert payload.city.as_of == payload.music.as_of == FETCHED.date()
    assert [b.repo for b in payload.city.buildings] == ["r"]


def test_payload_for_a_past_date_uses_the_snapshot() -> None:
    data = user(
        make_repo("old", 1, created_at=dt.datetime(2016, 1, 1, tzinfo=dt.UTC)),
        make_repo("new", 2, created_at=dt.datetime(2024, 1, 1, tzinfo=dt.UTC)),
    )

    payload = build_payload(data, FETCHED, dt.date(2020, 1, 1))

    assert payload.as_of == payload.city.as_of == dt.date(2020, 1, 1)
    assert [b.repo for b in payload.city.buildings] == ["old"]
    # The slider's range stays the whole history, whatever date is shown.
    assert payload.timeline.end == FETCHED.date()
