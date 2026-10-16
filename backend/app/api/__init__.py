from fastapi import APIRouter

from app.api import cities, health, jobs

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(cities.router)
api_router.include_router(jobs.router)
