import asyncio
from uuid import uuid4

import pytest
import redis
import redis.asyncio as aioredis

from app.storage.job_control import (
    DispatchPlan,
    acancel_job,
    reserve_dispatch,
    transition_status,
)


def _client():
    return redis.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True, socket_connect_timeout=1)


@pytest.fixture
def redis_job():
    r = _client()
    try:
        r.ping()
    except redis.RedisError:
        pytest.skip("redis not reachable on 127.0.0.1:6379")
    job_id = f"it_control_{uuid4().hex}"
    keys = [f"job:{job_id}", f"cancel:{job_id}", "stats:active"]
    r.delete(*keys)
    yield r, job_id
    r.delete(*keys)
    r.close()


def _cancel(job_id: str):
    async def run():
        r = aioredis.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
        try:
            return await acancel_job(r, job_id, ttl=60)
        finally:
            await r.aclose()

    return asyncio.run(run())


def test_reserved_ids_and_cancel_transition_are_one_atomic_protocol(redis_job):
    r, job_id = redis_job
    r.hset(f"job:{job_id}", mapping={"status": "queued"})
    r.hset("stats:active", "queued", 1)
    plan = DispatchPlan("evt", ("r0", "r1"), "cb", "stt")

    reservation = reserve_dispatch(r, job_id, plan)
    cancelled = _cancel(job_id)

    assert reservation.outcome == "reserved"
    assert cancelled.task_ids == ("r0", "r1", "cb", "stt")
    assert r.hget(f"job:{job_id}", "status") == "cancelled"
    assert r.get(f"cancel:{job_id}") == "1"
    assert int(r.hget("stats:active", "queued")) == 0


def test_cancel_winning_first_prevents_dispatch_reservation(redis_job):
    r, job_id = redis_job
    r.hset(f"job:{job_id}", mapping={"status": "queued"})
    r.hset("stats:active", "queued", 1)

    assert _cancel(job_id).outcome == "cancelled"
    result = reserve_dispatch(r, job_id, DispatchPlan("evt", ("r0",), "cb"))

    assert result.outcome == "skipped"
    assert r.hget(f"job:{job_id}", "rendition_ids") is None


def test_late_package_transition_cannot_overwrite_cancelled(redis_job):
    r, job_id = redis_job
    r.hset(f"job:{job_id}", mapping={"status": "queued"})
    r.hset("stats:active", "queued", 1)
    assert transition_status(r, job_id, "transcoding", caller="test") == "transcoding"

    assert _cancel(job_id).outcome == "cancelled"
    assert transition_status(r, job_id, "done", caller="late-package") is None
    assert r.hget(f"job:{job_id}", "status") == "cancelled"
