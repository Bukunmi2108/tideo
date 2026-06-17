import json
from typing import cast

from app.domain.state import transition
from app.events.producer import emit
from app.events.topics import (
    JOB_COMPLETED,
    JOB_STARTED,
    RENDITION_COMPLETED,
    RENDITION_STARTED,
)
from app.storage.state import get_sync_client, write_status
from app.workers.celery_app import app


def _fake_outputs(preset: str) -> dict:
    """Deterministic stand-in for real FFmpeg output (Phase 4 replaces this)."""
    h = int(preset[:-1]) if preset.endswith("p") and preset[:-1].isdigit() else 0
    return {"preset": preset, "output_bytes": h * 50_000, "encode_seconds": round(h / 10, 1)}


def run_lifecycle(presets: list[str], *, transition_to, emit) -> None:
    """The job's narrative — pure of I/O (transition_to + emit injected, so it's unit-testable).
    """
    if not transition_to("transcoding"):
        return
    emit(JOB_STARTED, {"renditions": presets})
    total = 0
    for p in presets:
        emit(RENDITION_STARTED, {"preset": p})
        out = _fake_outputs(p)
        total += out["output_bytes"]
        emit(RENDITION_COMPLETED, out)
    if transition_to("done"):
        emit(JOB_COMPLETED, {"renditions": len(presets), "output_bytes_total": total})


@app.task
def stub_job(job_id: str) -> dict:
    """Proves the producer works inside a forked
    worker"""
    r = get_sync_client()
    presets = json.loads(cast(str, r.hget(f"job:{job_id}", "presets")) or "[]")

    def transition_to(target: str) -> bool:
        cur = cast(str, r.hget(f"job:{job_id}", "status")) or ""
        nxt = transition(cur, target, job_id=job_id, caller="stub")
        if nxt is None:
            return False
        write_status(r, job_id, nxt)
        return True

    def emit_event(event_type: str, payload: dict) -> None:
        emit(event_type, job_id, payload)

    run_lifecycle(presets, transition_to=transition_to, emit=emit_event)
    return {"job_id": job_id, "renditions": presets}
