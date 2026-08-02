import asyncio
import json

from app.storage.job_control import (
    DispatchPlan,
    acancel_job,
    aqueue_job,
    atransition_status,
    reserve_dispatch,
    transition_status,
)
from app.storage.state import ACTIVE_DEADLINES, EVENT_OUTBOX, TERMINAL_OUTBOX


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
    assert key_count == 4
    assert args[:6] == (
        "job:j1", "stats:active", TERMINAL_OUTBOX, ACTIVE_DEADLINES, "queued", "transcoding"
    )
    assert args[-2:] == ("started_at", "now")


def test_transition_status_drops_late_terminal_without_writing():
    r = FakeRedis(status="cancelled")

    assert transition_status(r, "j1", "done", caller="test") is None
    assert r.calls == []


def test_terminal_transition_registers_projection_before_returning():
    r = FakeRedis(status="transcoding", result=[1, "done"])

    assert transition_status(r, "j1", "done", caller="package") == "done"

    _, key_count, args = r.calls[0]
    assert key_count == 4
    assert args[:4] == ("job:j1", "stats:active", TERMINAL_OUTBOX, ACTIVE_DEADLINES)
    assert args[8:10] == ("j1", "1")
    assert "finished_at" in args


def test_expected_async_transition_does_not_follow_an_advanced_job():
    class AsyncRedis:
        def __init__(self):
            self.calls = []

        async def eval(self, script, key_count, *args):
            self.calls.append((script, key_count, args))
            return [0, "awaiting_choice"]

    r = AsyncRedis()
    result = asyncio.run(
        atransition_status(
            r,
            "j1",
            "failed",
            caller="upload",
            expected="inspecting",
        )
    )

    assert result is None
    assert len(r.calls) == 1


def test_queue_job_writes_state_and_exact_event_in_one_eval():
    class AsyncRedis:
        def __init__(self):
            self.calls = []

        async def eval(self, script, key_count, *args):
            self.calls.append((script, key_count, args))
            return [1, "queued"]

    r = AsyncRedis()
    event_json = '{"event_id":"evt-1"}'

    result = asyncio.run(
        aqueue_job(
            r,
            "j1",
            presets='["720p"]',
            subtitles=True,
            event_id="evt-1",
            event_json=event_json,
        )
    )

    assert result == "queued"
    _, key_count, args = r.calls[0]
    assert key_count == 3
    assert args[:3] == ("job:j1", "stats:active", EVENT_OUTBOX)
    assert args[3:] == ('["720p"]', "true", "evt-1", event_json)


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
