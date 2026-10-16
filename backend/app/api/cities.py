import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from app.api.deps import (
    CityCacheDep,
    ClientIpDep,
    GitHubDep,
    QueueDep,
    RateLimiterDep,
    SessionDep,
    SettingsDep,
)
from app.schemas.cities import CityResponse
from app.schemas.errors import ErrorResponse
from app.schemas.jobs import JobResponse
from app.services.cities import CityReady, request_city
from app.services.city_payload import get_city_json
from app.usernames import LOGIN_MAX_LENGTH, LOGIN_REGEX

router = APIRouter(tags=["cities"])

Username = Annotated[str, Path(pattern=LOGIN_REGEX, max_length=LOGIN_MAX_LENGTH)]
Until = Annotated[
    dt.date | None,
    Query(description="Show the city as it was at the end of this day (YYYY-MM-DD)."),
]


def _json(body: str, status_code: int = status.HTTP_200_OK) -> Response:
    # The payload is already JSON text (often straight from the cache), so send it as-is
    # rather than parsing and re-serializing it.
    return Response(content=body, status_code=status_code, media_type="application/json")


@router.post(
    "/cities/{username}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=None,
    responses={
        200: {"model": CityResponse, "description": "Data is fresh: here is the city"},
        202: {"model": JobResponse, "description": "A job is queued or running; poll it"},
        404: {"model": ErrorResponse, "description": "No such GitHub user"},
        429: {"model": ErrorResponse, "description": "Too many new cities from this client"},
        503: {"model": ErrorResponse, "description": "GitHub rate limited or unavailable"},
    },
)
async def create_city(
    username: Username,
    response: Response,
    session: SessionDep,
    github: GitHubDep,
    queue: QueueDep,
    cache: CityCacheDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    client_ip: ClientIpDep,
) -> Response | JobResponse:
    result = await request_city(
        session,
        username,
        github=github,
        queue=queue,
        settings=settings,
        limiter=limiter,
        client_ip=client_ip,
    )
    if isinstance(result, CityReady):
        return _json(await get_city_json(session, cache, result.username))
    response.headers["Location"] = f"/api/jobs/{result.job.id}"
    return JobResponse.model_validate(result.job)


@router.get(
    "/cities/{username}",
    response_model=CityResponse,
    responses={404: {"model": ErrorResponse, "description": "This user's city was never built"}},
)
async def read_city(
    username: Username, session: SessionDep, cache: CityCacheDep, until: Until = None
) -> Response:
    return _json(await get_city_json(session, cache, username, until))
