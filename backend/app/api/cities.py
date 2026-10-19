import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from app.api.deps import (
    CityCacheDep,
    CurrentUserDep,
    GitHubDep,
    QueueDep,
    RateLimiterDep,
    RequesterDep,
    SessionDep,
    SettingsDep,
)
from app.errors import PrivateCityForbiddenError
from app.schemas.cities import CityResponse
from app.schemas.errors import ErrorResponse
from app.schemas.jobs import JobResponse
from app.services.cities import CityReady, request_city
from app.services.city_payload import get_city_json
from app.usernames import LOGIN_MAX_LENGTH, LOGIN_REGEX, normalize_login

router = APIRouter(tags=["cities"])

Username = Annotated[str, Path(pattern=LOGIN_REGEX, max_length=LOGIN_MAX_LENGTH)]
Until = Annotated[
    dt.date | None,
    Query(description="Show the city as it was at the end of this day (YYYY-MM-DD)."),
]
Private = Annotated[
    bool,
    Query(description="Include the private repos you shared. Only for your own city."),
]

# A city with private repos is for one person's eyes: no shared cache may keep a copy.
PRIVATE_HEADERS = {"Cache-Control": "private, no-store"}


def _json(
    body: str, status_code: int = status.HTTP_200_OK, headers: dict[str, str] | None = None
) -> Response:
    # The payload is already JSON text (often straight from the cache), so send it as-is
    # rather than parsing and re-serializing it.
    return Response(
        content=body, status_code=status_code, media_type="application/json", headers=headers
    )


@router.post(
    "/cities/{username}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=None,
    responses={
        200: {"model": CityResponse, "description": "Data is fresh: here is the city"},
        202: {"model": JobResponse, "description": "A job is queued or running; poll it"},
        404: {"model": ErrorResponse, "description": "No such GitHub user"},
        429: {"model": ErrorResponse, "description": "Too many new cities from this visitor"},
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
    requester: RequesterDep,
) -> Response | JobResponse:
    result = await request_city(
        session,
        username,
        github=github,
        queue=queue,
        settings=settings,
        limiter=limiter,
        requester=requester,
    )
    if isinstance(result, CityReady):
        return _json(await get_city_json(session, cache, result.username))
    response.headers["Location"] = f"/api/jobs/{result.job.id}"
    return JobResponse.model_validate(result.job)


@router.get(
    "/cities/{username}",
    response_model=CityResponse,
    responses={
        403: {"model": ErrorResponse, "description": "Private repos of someone else's city"},
        404: {"model": ErrorResponse, "description": "This user's city was never built"},
    },
)
async def read_city(
    username: Username,
    session: SessionDep,
    cache: CityCacheDep,
    user: CurrentUserDep,
    until: Until = None,
    private: Private = False,
) -> Response:
    if not private:
        return _json(await get_city_json(session, cache, username, until))
    if user is None or normalize_login(user.login) != normalize_login(username):
        raise PrivateCityForbiddenError("Only the owner of a city can see its private repos.")
    body = await get_city_json(session, cache, username, until, include_private=True)
    return _json(body, headers=PRIVATE_HEADERS)
