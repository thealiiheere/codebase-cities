import datetime as dt

from pydantic import BaseModel


class CityReadyResponse(BaseModel):
    """Returned when stored data is fresh. Phase 5 replaces this with the city and music."""

    username: str
    fetched_at: dt.datetime
