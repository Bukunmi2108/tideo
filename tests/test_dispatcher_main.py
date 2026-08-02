import json

from redis.exceptions import RedisError

import app.dispatcher.__main__ as dispatcher
from app.events.envelope import Envelope
from app.events.topics import JOB_CREATED, TOPIC


class Message:
    def __init__(self, value: bytes):
        self._value = value

    def error(self):
        return None

    def value(self):
        return self._value

    def topic(self):
        return TOPIC

    def partition(self):
        return 1

    def offset(self):
        return 2


class Consumer:
    def __init__(self, messages):
        self.messages = list(messages)
        self.commits = []
        self.seeks = []
        self.closed = False

    def subscribe(self, _topics):
        pass

    def poll(self, _timeout):
        message = self.messages.pop(0)
        if not self.messages:
            dispatcher._running = False
        return message

    def commit(self, **kwargs):
        self.commits.append(kwargs)

    def seek(self, partition):
        self.seeks.append(partition)

    def close(self):
        self.closed = True


def run(monkeypatch, messages):
    consumer = Consumer(messages)
    monkeypatch.setattr(dispatcher, "Consumer", lambda _config: consumer)
    monkeypatch.setattr(dispatcher.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(dispatcher, "get_sync_client", lambda: object())
    monkeypatch.setattr(dispatcher, "drain_outbox", lambda _r: 0)
    monkeypatch.setattr(dispatcher, "_running", True)
    dispatcher.run()
    return consumer


def test_heartbeat_is_throttled_during_rapid_polls(monkeypatch):
    heartbeats = []
    times = iter((0.0, 5.0, 10.0, 15.0))
    monkeypatch.setattr(dispatcher, "_heartbeat", lambda: heartbeats.append(True))
    monkeypatch.setattr(dispatcher.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(dispatcher.config, "dispatcher_heartbeat_ttl", 30)

    consumer = run(monkeypatch, [None, None, None])

    assert len(heartbeats) == 2
    assert consumer.closed is True


def test_poison_event_is_committed(monkeypatch):
    monkeypatch.setattr(dispatcher, "_heartbeat", lambda: None)

    consumer = run(monkeypatch, [Message(b"not-json")])

    assert len(consumer.commits) == 1
    assert consumer.commits[0]["asynchronous"] is False
    assert consumer.closed is True


def test_infra_failure_seeks_without_committing(monkeypatch):
    payload = Envelope(
        JOB_CREATED,
        "j1",
        {"presets": ["720p"], "subtitles": False, "source_duration": 30.0},
    ).to_json().encode()
    monkeypatch.setattr(dispatcher, "_heartbeat", lambda: None)
    monkeypatch.setattr(
        dispatcher,
        "process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RedisError("down")),
    )
    monkeypatch.setattr(dispatcher.time, "sleep", lambda _seconds: None)

    consumer = run(monkeypatch, [Message(payload)])

    assert consumer.commits == []
    assert len(consumer.seeks) == 1
    assert consumer.seeks[0].topic == TOPIC
    assert consumer.seeks[0].partition == 1
    assert consumer.seeks[0].offset == 2


def test_invalid_created_event_is_committed_before_next_valid_event(monkeypatch):
    invalid = json.dumps({
        "event_id": "00000000-0000-4000-8000-000000000001",
        "event_type": JOB_CREATED,
        "job_id": "j1",
        "timestamp": "2026-08-02T10:00:00+00:00",
        "producer": "test",
        "schema_version": 1,
        "payload": {},
    }).encode()
    valid = Envelope(
        JOB_CREATED,
        "j2",
        {"presets": ["480p"], "subtitles": False, "source_duration": 30.0},
    ).to_json().encode()
    processed = []
    monkeypatch.setattr(dispatcher, "_heartbeat", lambda: None)
    monkeypatch.setattr(
        dispatcher,
        "process",
        lambda env, **_kwargs: processed.append(env["job_id"]) or "dispatched",
    )

    consumer = run(monkeypatch, [Message(invalid), Message(valid)])

    assert len(consumer.commits) == 2
    assert processed == ["j2"]
    assert consumer.closed is True
