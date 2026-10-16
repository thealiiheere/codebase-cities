from fastapi import APIRouter

from app.api.deps import RedisDep, SessionDep
from app.errors import ServiceUnavailableError
from app.schemas.errors import ErrorResponse
from app.schemas.health import HealthResponse
from app.services.health import check_health

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    responses={503: {"model": ErrorResponse, "description": "A dependency is unreachable"}},
)
async def health(session: SessionDep, redis: RedisDep) -> HealthResponse:
    report = await check_health(session, redis)
    if not report.healthy:
        raise ServiceUnavailableError(f"Unavailable: {', '.join(report.failing)}")
    return HealthResponse()
