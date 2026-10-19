import datetime as dt

from pydantic import BaseModel

from app.mapping import CityLayout, MusicParams


class Timeline(BaseModel):
    """The range the timeline slider can cover."""

    # When the GitHub account was created.
    start: dt.date
    # The day the data was fetched: the latest point the timeline can show.
    end: dt.date


class CityResponse(BaseModel):
    username: str
    fetched_at: dt.datetime
    # The day the city depicts: `until` if given, otherwise the fetch day.
    as_of: dt.date
    # The requested timeline date, or null for the latest city.
    until: dt.date | None
    timeline: Timeline
    # How many of the buildings are private repos (only ever non-zero for their owner).
    private_repos: int = 0
    city: CityLayout
    music: MusicParams
