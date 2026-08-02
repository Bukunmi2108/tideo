from app.events import producer
from app.events.envelope import Envelope


def test_emit_success_publishes_envelope(monkeypatch):
    sent = []
    monkeypatch.setattr(producer, "publish", lambda env: sent.append(env))

    ok = producer.emit("rendition.completed", "j1", {"preset": "720p", "output_bytes": 9})

    assert ok is True
    assert len(sent) == 1
    assert sent[0].event_type == "rendition.completed"
    assert sent[0].job_id == "j1"
    assert sent[0].payload == {"preset": "720p", "output_bytes": 9}


def test_emit_swallows_producer_error(monkeypatch):
    def boom(_env):
        raise RuntimeError("kafka local queue full")

    monkeypatch.setattr(producer, "publish", boom)

    ok = producer.emit("job.started", "j2", {})

    assert ok is False


class ConfirmingProducer:
    def __init__(self, *, error=None, remaining=0):
        self.error = error
        self.remaining = remaining
        self.callback = None

    def produce(self, _topic, **kwargs):
        self.callback = kwargs["on_delivery"]

    def flush(self, _timeout):
        if self.callback is not None and self.remaining == 0:
            self.callback(self.error, object())
        return self.remaining


def test_confirmed_publish_waits_for_delivery(monkeypatch):
    fake = ConfirmingProducer()
    monkeypatch.setattr(producer, "get_producer", lambda: fake)

    producer.publish_confirmed(Envelope("job.created", "j1", {}))

    assert fake.callback is not None


def test_confirmed_publish_raises_on_delivery_failure(monkeypatch):
    fake = ConfirmingProducer(error=RuntimeError("broker rejected event"))
    monkeypatch.setattr(producer, "get_producer", lambda: fake)

    try:
        producer.publish_confirmed(Envelope("job.created", "j1", {}))
    except RuntimeError as exc:
        assert "broker rejected event" in str(exc)
    else:
        raise AssertionError("delivery failure must be surfaced")


def test_confirmed_publish_raises_when_flush_times_out(monkeypatch):
    fake = ConfirmingProducer(remaining=1)
    monkeypatch.setattr(producer, "get_producer", lambda: fake)

    try:
        producer.publish_confirmed(Envelope("job.created", "j1", {}), timeout=0.01)
    except TimeoutError:
        pass
    else:
        raise AssertionError("undelivered event must remain in the outbox")


def test_flush_reports_undelivered_messages(monkeypatch):
    fake = ConfirmingProducer(remaining=2)
    monkeypatch.setattr(producer, "_producer", fake)

    assert producer.flush_producer(timeout=0.01) == 2


def test_worker_fork_resets_inherited_producer(monkeypatch):
    monkeypatch.setattr(producer, "_producer", object())

    producer._reset_after_fork()

    assert producer._producer is None


def test_worker_shutdown_flushes(monkeypatch):
    calls = []
    monkeypatch.setattr(producer, "flush_producer", lambda: calls.append(True) or 0)

    producer._flush_on_shutdown()

    assert calls == [True]
