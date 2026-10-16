import uuid

import pytest
from arq.connections import ArqRedis
from arq.jobs import Job as ArqJob
from redis.asyncio import Redis

from app.cache import ETAG_KEY_PREFIX, ETAG_TTL_SECONDS, RedisEtagStore
from app.github.etag import CachedResponse
from app.queue import INGEST_USER_JOB, ArqJobQueue
from app.worker import WorkerSettings, shutdown


def test_worker_runs_the_job_the_queue_enqueues() -> None:
    assert [function.__name__ for function in WorkerSettings.functions] == [INGEST_USER_JOB]


async def test_worker_shutdown_tolerates_a_failed_startup() -> None:
    await shutdown({})  # ARQ calls shutdown even if startup never ran.


@pytest.mark.integration
async def test_etag_store_round_trip(redis_client: Redis) -> None:
    store = RedisEtagStore(redis_client)
    cached = CachedResponse(etag='W/"abc"', body={"login": "octocat", "list": [1, 2]})

    assert await store.get("/users/octocat") is None
    await store.set("/users/octocat", cached)

    assert await store.get("/users/octocat") == cached
    ttl = await redis_client.ttl(ETAG_KEY_PREFIX + "/users/octocat")
    assert 0 < ttl <= ETAG_TTL_SECONDS


@pytest.mark.integration
async def test_queue_enqueues_one_arq_job_per_job_id(
    test_redis_url: str, redis_client: Redis
) -> None:
    arq_redis = ArqRedis.from_url(test_redis_url)
    queue = ArqJobQueue(arq_redis)
    job_id = uuid.uuid4()

    await queue.enqueue_ingestion(job_id)
    await queue.enqueue_ingestion(job_id)  # ARQ ignores a second job with the same id.

    info = await ArqJob(str(job_id), arq_redis).info()
    assert info is not None
    assert info.function == INGEST_USER_JOB
    assert info.args == (str(job_id),)
    assert len(await arq_redis.queued_jobs()) == 1
    await queue.aclose()
