from app.events import producer


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
