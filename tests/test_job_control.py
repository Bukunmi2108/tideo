import asyncio
import json

from app.storage.job_control import (
    DispatchPlan,
    acancel_job,
    reserve_dispatch,
    transition_status,
)


class FakeRedis:
    def __init__(self, *, status="queued", result=None):
        self.status = status
        self.result = result
        self.calls = []

    def hget(self, key, field):
        return self.status

    def eval(self, script, key_count, *args):
        self.calls.append((script, key_count, args))
        return self.result


def test_transition_status_uses_atomic_compare_and_set():
    r = FakeRedis(status="queued", result=[1, "transcoding"])

    assert transition_status(r, "j1", "transcoding", caller="test", extra={"started_at": "now"}) == "transcoding"
    _, key_count, args = r.calls[0]
    assert key_count == 2
    assert args[:4] == ("job:j1", "stats:active", "queued", "transcoding")
    assert args[-2:] == ("started_at", "now")


def test_transition_status_drops_late_terminal_without_writing():
    r = FakeRedis(status="cancelled")

    assert transition_status(r, "j1", "done", caller="test") is None
    assert r.calls == []


def test_reserve_dispatch_returns_the_plan_redis_committed():
    existing = ["r-old"]
    r = FakeRedis(result=["existing", "queued", "evt", json.dumps(existing), "cb-old", "stt-old"])
    proposed = DispatchPlan("evt", ("r-new",), "cb-new", "stt-new")

    result = reserve_dispatch(r, "j1", proposed)

    assert result.outcome == "existing"
    assert result.plan == DispatchPlan("evt", ("r-old",), "cb-old", "stt-old")


def test_cancel_returns_every_reserved_task_id():
    class AsyncRedis:
        async def eval(self, *_args):
            return ["cancelled", "transcoding", '["r0", "r1"]', "cb", "stt"]

    result = asyncio.run(acancel_job(AsyncRedis(), "j1", ttl=60))

    assert result.outcome == "cancelled"
    assert result.previous_status == "transcoding"
    assert result.task_ids == ("r0", "r1", "cb", "stt")
