from fastapi import APIRouter

from app.api import auth, cities, health, jobs, me

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(cities.router)
api_router.include_router(jobs.router)
api_router.include_router(me.router)
