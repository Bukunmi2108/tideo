import json

import pytest

from app.dispatcher.guard import claim
from app.dispatcher.handler import BadEvent, parse_event, process
from app.events.topics import JOB_CREATED


class FakeRedis:
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
    assert claim("evt-1") is True
    assert claim("evt-1") is False
    assert claim("evt-2") is True


def test_release_makes_event_claimable_again(monkeypatch):
    import app.dispatcher.guard as g
    from app.dispatcher.guard import release

    fake = FakeRedis()
    monkeypatch.setattr(g, "get_sync_client", lambda: fake)
    assert claim("evt-1") is True
    assert claim("evt-1") is False
    release("evt-1")
    assert claim("evt-1") is True


def _env(**kw):
    base = {"event_id": "e1", "event_type": JOB_CREATED, "job_id": "j1"}
    base.update(kw)
    return base


def test_parse_valid_envelope():
    raw = json.dumps(_env()).encode()
    assert parse_event(raw)["event_type"] == JOB_CREATED


@pytest.mark.parametrize(
    "raw", [None, b"", b"not-json", b"{not valid", b"[]", b"\xff\xfe"]
)
def test_parse_rejects_garbage(raw):
    with pytest.raises(BadEvent):
        parse_event(raw)


@pytest.mark.parametrize("event_id", [None, "", " ", 1])
def test_parse_rejects_invalid_required_field(event_id):
    raw = json.dumps(_env(event_id=event_id)).encode()
    with pytest.raises(BadEvent):
        parse_event(raw)


def test_parse_rejects_missing_required_field():
    raw = json.dumps({"event_type": JOB_CREATED, "job_id": "j1"}).encode()
    with pytest.raises(BadEvent):
        parse_event(raw)


def test_process_dispatches_new_job_created():
    enq = []
    action = process(
        _env(),
        claim=lambda _id: True,
        enqueue=lambda e: enq.append(e["job_id"]),
        release=lambda _id: None,
    )
    assert action == "dispatched"
    assert enq == ["j1"]


def test_process_skips_duplicate_without_enqueue():
    enq = []
    action = process(
        _env(),
        claim=lambda _id: False,
        enqueue=lambda e: enq.append(e["job_id"]),
        release=lambda _id: None,
    )
    assert action == "duplicate"
    assert enq == []


def test_process_skips_non_dispatchable_without_claiming():
    claimed = []
    enq = []
    action = process(
        _env(event_type="rendition.completed"),
        claim=lambda _id: claimed.append(_id) or True,
        enqueue=lambda e: enq.append(e["job_id"]),
        release=lambda _id: None,
    )
    assert action == "skipped"
    assert claimed == [] and enq == []


def test_process_propagates_redis_error_for_fail_closed():
    from redis.exceptions import RedisError

    def boom(_id):
        raise RedisError("redis down")

    with pytest.raises(RedisError):
        process(
            _env(),
            claim=boom,
            enqueue=lambda e: None,
            release=lambda _id: None,
        )


def test_process_releases_claim_when_enqueue_fails():
    released = []

    def enqueue_boom(_e):
        raise RuntimeError("broker unreachable")

    with pytest.raises(RuntimeError):
        process(
            _env(),
            claim=lambda _id: True,
            enqueue=enqueue_boom,
            release=lambda _id: released.append(_id),
        )
    assert released == ["e1"]
