from app.events import producer
from app.events.topics import (
    JOB_COMPLETED,
    JOB_STARTED,
    RENDITION_COMPLETED,
    RENDITION_STARTED,
)
from app.workers.tasks.dispatch_stub import run_lifecycle


# ---------- emit() fail-open ----------

def test_emit_success_publishes_envelope(monkeypatch):
    sent = []
    monkeypatch.setattr(producer, "publish", lambda env: sent.append(env))
    ok = producer.emit("rendition.completed", "j1", {"preset": "720p", "output_bytes": 9})
    assert ok is True
    assert len(sent) == 1
    assert sent[0].event_type == "rendition.completed"
    assert sent[0].job_id == "j1"
    assert sent[0].payload == {"preset": "720p", "output_bytes": 9}


def test_emit_swallows_producer_error_and_counts(monkeypatch):
    def boom(_env):
        raise RuntimeError("kafka local queue full")

    monkeypatch.setattr(producer, "publish", boom)
    before = producer.emit_failures()
    ok = producer.emit("job.started", "j2", {})         # must NOT raise
    assert ok is False
    assert producer.emit_failures() == before + 1       # counted, work carries on


# ---------- run_lifecycle narrative ----------

class Recorder:
    def __init__(self, terminal_at=None):
        self.events = []        # list[(event_type, payload)]
        self.transitions = []   # list[target]
        self.terminal_at = terminal_at or set()

    def emit(self, event_type, payload):
        self.events.append((event_type, payload))

    def transition_to(self, target):
        self.transitions.append(target)
        applied = target not in self.terminal_at
        return applied


def test_lifecycle_emits_ordered_narrative():
    rec = Recorder()
    run_lifecycle(["720p", "480p"], transition_to=rec.transition_to, emit=rec.emit)

    assert [e for e, _ in rec.events] == [
        JOB_STARTED,
        RENDITION_STARTED, RENDITION_COMPLETED,
        RENDITION_STARTED, RENDITION_COMPLETED,
        JOB_COMPLETED,
    ]
    assert rec.transitions == ["transcoding", "done"]


def test_lifecycle_completion_payload_aggregates_outputs():
    rec = Recorder()
    run_lifecycle(["720p", "480p"], transition_to=rec.transition_to, emit=rec.emit)

    completes = [p for e, p in rec.events if e == RENDITION_COMPLETED]
    assert all("output_bytes" in p and "encode_seconds" in p for p in completes)

    job_done = next(p for e, p in rec.events if e == JOB_COMPLETED)
    assert job_done["renditions"] == 2
    assert job_done["output_bytes_total"] == sum(p["output_bytes"] for p in completes)


def test_lifecycle_on_terminal_job_emits_nothing():
    # job already terminal (e.g. cancelled): the first transition doesn't apply -> no phantom events
    rec = Recorder(terminal_at={"transcoding"})
    run_lifecycle(["720p"], transition_to=rec.transition_to, emit=rec.emit)

    assert rec.events == []                  # announced nothing
    assert rec.transitions == ["transcoding"]  # tried once, bailed
