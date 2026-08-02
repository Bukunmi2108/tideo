import json

import pytest
from redis.exceptions import RedisError

from app.events import outbox
from app.events.envelope import Envelope
from app.storage.state import EVENT_OUTBOX


class FakeRedis:
    def __init__(self, events):
        self.events = dict(events)

    def hscan(self, key, cursor=0, count=None) -> tuple[int, dict]:
        assert key == EVENT_OUTBOX
        return 0, dict(list(self.events.items())[:count])

    def hdel(self, key, event_id):
        assert key == EVENT_OUTBOX
        self.events.pop(event_id, None)


def _stored_event() -> Envelope:
    return Envelope("job.created", "j1", {
        "presets": ["720p"], "subtitles": False, "source_duration": 30.0,
    })


def test_drain_deletes_only_after_confirmed_delivery(monkeypatch):
    event = _stored_event()
    r = FakeRedis({event.event_id: event.to_json()})
    delivered = []
    monkeypatch.setattr(outbox, "publish_confirmed", lambda env: delivered.append(env))

    assert outbox.drain(r) == 1

    assert delivered[0].event_id == event.event_id
    assert delivered[0].payload == event.payload
    assert r.events == {}


def test_drain_retains_event_when_delivery_fails(monkeypatch):
    event = _stored_event()
    r = FakeRedis({event.event_id: event.to_json()})

    def fail(_env):
        raise RuntimeError("kafka unavailable")

    monkeypatch.setattr(outbox, "publish_confirmed", fail)

    assert outbox.drain(r) == 0
    assert json.loads(r.events[event.event_id])["event_id"] == event.event_id


def test_drain_retains_acknowledged_event_when_delete_fails(monkeypatch):
    class DeleteFails(FakeRedis):
        def hdel(self, key, event_id):
            raise RedisError("redis unavailable after Kafka ack")

    event = _stored_event()
    r = DeleteFails({event.event_id: event.to_json()})
    delivered = []
    monkeypatch.setattr(outbox, "publish_confirmed", lambda env: delivered.append(env))

    with pytest.raises(RedisError):
        outbox.drain(r)

    assert [env.event_id for env in delivered] == [event.event_id]
    assert event.event_id in r.events


def test_drain_advances_past_empty_scan_pages(monkeypatch):
    class PagedRedis(FakeRedis):
        def hscan(self, key, cursor=0, count=None):
            assert key == EVENT_OUTBOX
            if cursor == 0:
                return 4, {}
            return 0, self.events.copy()

    event = _stored_event()
    r = PagedRedis({event.event_id: event.to_json()})
    delivered = []
    monkeypatch.setattr(outbox, "publish_confirmed", lambda env: delivered.append(env))

    assert outbox.drain(r) == 1
    assert [env.event_id for env in delivered] == [event.event_id]
