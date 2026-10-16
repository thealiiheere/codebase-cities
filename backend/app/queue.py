"""Background job queue. Services depend on the JobQueue protocol; ARQ is the implementation."""

import uuid
from typing import Protocol

from arq.connections import ArqRedis

from app.config import Settings

# Name of the worker function that ingests a user (see app/worker.py).
INGEST_USER_JOB = "ingest_user"


class JobQueue(Protocol):
    async def enqueue_ingestion(self, job_id: uuid.UUID) -> None: ...


class ArqJobQueue:
    def __init__(self, redis: ArqRedis) -> None:
        self._redis = redis

    async def enqueue_ingestion(self, job_id: uuid.UUID) -> None:
        # Reusing our job id as ARQ's id makes ARQ ignore a second enqueue of the same job,
        # and makes ARQ's logs line up with our jobs table.
        await self._redis.enqueue_job(INGEST_USER_JOB, str(job_id), _job_id=str(job_id))

    async def aclose(self) -> None:
        await self._redis.aclose()


def create_job_queue(settings: Settings) -> ArqJobQueue:
    # Built lazily from a URL rather than with arq.create_pool(), which connects (and retries for
    # several seconds) immediately; the API should start even while Redis is briefly down.
    return ArqJobQueue(ArqRedis.from_url(settings.redis_url))
