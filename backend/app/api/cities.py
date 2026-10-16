from typing import Annotated

from fastapi import APIRouter, Path, Response, status

from app.api.deps import GitHubDep, QueueDep, SessionDep, SettingsDep
from app.schemas.cities import CityReadyResponse
from app.schemas.errors import ErrorResponse
from app.schemas.jobs import JobResponse
from app.services.cities import CityReady, request_city
from app.usernames import LOGIN_MAX_LENGTH, LOGIN_REGEX

router = APIRouter(tags=["cities"])

Username = Annotated[str, Path(pattern=LOGIN_REGEX, max_length=LOGIN_MAX_LENGTH)]


@router.post(
    "/cities/{username}",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        200: {"model": CityReadyResponse, "description": "Data is fresh; nothing to do"},
        202: {"model": JobResponse, "description": "A job is queued or running; poll it"},
        404: {"model": ErrorResponse, "description": "No such GitHub user"},
        503: {"model": ErrorResponse, "description": "GitHub rate limited or unavailable"},
    },
)
async def create_city(
    username: Username,
    response: Response,
    session: SessionDep,
    github: GitHubDep,
    queue: QueueDep,
    settings: SettingsDep,
) -> JobResponse | CityReadyResponse:
    result = await request_city(session, username, github=github, queue=queue, settings=settings)
    if isinstance(result, CityReady):
        response.status_code = status.HTTP_200_OK
        return CityReadyResponse(username=result.username, fetched_at=result.fetched_at)
    response.headers["Location"] = f"/api/jobs/{result.job.id}"
    return JobResponse.model_validate(result.job)
