"""The signed-in user's own things. Every route here needs a session."""

from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.cities import PRIVATE_HEADERS
from app.api.deps import (
    CityCacheDep,
    CurrentUserDep,
    QueueDep,
    RateLimiterDep,
    RequesterDep,
    SessionDep,
    SettingsDep,
)
from app.errors import NotSignedInError, PrivateReposDisabledError
from app.schemas.cities import CityResponse
from app.schemas.errors import ErrorResponse
from app.schemas.jobs import JobResponse
from app.services.cities import CityReady
from app.services.city_payload import get_city_json
from app.services.private_cities import request_private_city

router = APIRouter(prefix="/me", tags=["me"])


@router.post(
    "/private-city",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=None,
    responses={
        200: {"model": CityResponse, "description": "Your city, with your private repos"},
        202: {"model": JobResponse, "description": "Fetching your private repos; poll the job"},
        401: {"model": ErrorResponse, "description": "Not signed in"},
        404: {"model": ErrorResponse, "description": "Private repos aren't set up here"},
        409: {"model": ErrorResponse, "description": "Sign in again (no usable GitHub token)"},
    },
)
async def private_city(
    response: Response,
    user: CurrentUserDep,
    session: SessionDep,
    queue: QueueDep,
    cache: CityCacheDep,
    settings: SettingsDep,
    limiter: RateLimiterDep,
    requester: RequesterDep,
    refresh: Annotated[
        bool, Query(description="Fetch again even if fresh (e.g. after sharing more repos).")
    ] = False,
) -> Response | JobResponse:
    """Your city with the private repos you shared, fetching them first if needed."""
    if user is None:
        raise NotSignedInError("Sign in with GitHub to see your private repos.")
    if not settings.private_repos_enabled:
        raise PrivateReposDisabledError("This server isn't set up to show private repos.")
    result = await request_private_city(
        session,
        user,
        queue=queue,
        settings=settings,
        limiter=limiter,
        requester=requester,
        refresh=refresh,
    )
    response.headers.update(PRIVATE_HEADERS)
    if isinstance(result, CityReady):
        body = await get_city_json(session, cache, result.username, include_private=True)
        return Response(body, media_type="application/json", headers=PRIVATE_HEADERS)
    response.headers["Location"] = f"/api/jobs/{result.job.id}"
    return JobResponse.model_validate(result.job)
