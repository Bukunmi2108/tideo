import json

import pytest

from app.dispatcher.guard import claim
from app.dispatcher.handler import BadEvent, is_dispatchable, parse_event, process
from app.events.topics import JOB_CREATED


# ---------- guard (claim) ----------

class FakeRedis:
    """Async/sync-agnostic minimal Redis with real SET NX semantics."""

    def __init__(self):
        self.kv = {}

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    def delete(self, k):
        self.kv.pop(k, None)


def test_claim_first_wins_second_loses(monkeypatch):
    import app.dispatcher.guard as g
    fake = FakeRedis()
    monkeypatch.setattr(g, "get_sync_client", lambda: fake)
    assert claim("evt-1") is True       # first sighting -> dispatch
    assert claim("evt-1") is False      # redelivery -> skip
    assert claim("evt-2") is True       # different event -> independent


def test_release_makes_event_claimable_again(monkeypatch):
    import app.dispatcher.guard as g
    from app.dispatcher.guard import release
    fake = FakeRedis()
    monkeypatch.setattr(g, "get_sync_client", lambda: fake)
    assert claim("evt-1") is True
    assert claim("evt-1") is False      # already claimed
    release("evt-1")                    # enqueue failed -> undo the claim
    assert claim("evt-1") is True       # retryable again, not burned


# ---------- parse_event ----------

def _env(**kw):
    base = {"event_id": "e1", "event_type": JOB_CREATED, "job_id": "j1"}
    base.update(kw)
    return base


def test_parse_valid_envelope():
    raw = json.dumps(_env()).encode()
    assert parse_event(raw)["event_type"] == JOB_CREATED


@pytest.mark.parametrize("raw", [None, b"", b"not-json", b"{not valid", b"[]", b"\xff\xfe"])
def test_parse_rejects_garbage(raw):
    with pytest.raises(BadEvent):
        parse_event(raw)


def test_parse_rejects_missing_required_field():
    raw = json.dumps({"event_type": JOB_CREATED, "job_id": "j1"}).encode()  # no event_id
    with pytest.raises(BadEvent):
        parse_event(raw)


# ---------- is_dispatchable ----------

def test_only_job_created_is_dispatchable():
    assert is_dispatchable(_env(event_type=JOB_CREATED)) is True
    assert is_dispatchable(_env(event_type="rendition.completed")) is False
    assert is_dispatchable(_env(event_type="job.started")) is False


# ---------- process (the decision, deps injected) ----------

def test_process_dispatches_new_job_created():
    enq = []
    action = process(_env(), claim=lambda _id: True, enqueue=lambda e: enq.append(e["job_id"]))
    assert action == "dispatched"
    assert enq == ["j1"]


def test_process_skips_duplicate_without_enqueue():
    enq = []
    action = process(_env(), claim=lambda _id: False, enqueue=lambda e: enq.append(e["job_id"]))
    assert action == "duplicate"
    assert enq == []                    # guard lost -> no work enqueued


def test_process_skips_non_dispatchable_without_claiming():
    claimed = []
    enq = []
    action = process(
        _env(event_type="rendition.completed"),
        claim=lambda _id: claimed.append(_id) or True,
        enqueue=lambda e: enq.append(e["job_id"]),  # not reached
    )
    assert action == "skipped"
    assert claimed == [] and enq == []  # not ours -> never even claim


def test_process_propagates_redis_error_for_fail_closed():
    from redis.exceptions import RedisError

    def boom(_id):
        raise RedisError("redis down")

    with pytest.raises(RedisError):
        process(_env(), claim=boom, enqueue=lambda e: None)


def test_process_releases_claim_when_enqueue_fails():
    """Broker-down enqueue must not burn the event: release the claim, then re-raise to stall."""
    released = []

    def enqueue_boom(_e):
        raise RuntimeError("broker unreachable")

    with pytest.raises(RuntimeError):
        process(_env(), claim=lambda _id: True, enqueue=enqueue_boom,
                release=lambda _id: released.append(_id))
    assert released == ["e1"]   # claim undone -> re-consume retries instead of skipping as duplicate
