import uuid

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.schemas.errors import ErrorResponse
from app.schemas.jobs import JobResponse
from app.services.jobs import get_job

router = APIRouter(tags=["jobs"])


@router.get("/jobs/{job_id}", responses={404: {"model": ErrorResponse}})
async def read_job(job_id: uuid.UUID, session: SessionDep) -> JobResponse:
    return JobResponse.model_validate(await get_job(session, job_id))
