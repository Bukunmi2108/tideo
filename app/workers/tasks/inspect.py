import json
from dataclasses import asdict

from app.core.config import config
from app.core.logging import bind_job, get_logger
from app.domain import recommend
from app.storage import terminal_outbox
from app.storage.job_control import transition_status
from app.storage.state import get_sync_client
from app.workers import ffprobe
from app.workers.base import InspectTask
from app.workers.celery_app import app

log = get_logger()


@app.task(base=InspectTask)
def probe(job_id: str, src: str) -> dict:
    bind_job(job_id)
    r = get_sync_client()
    try:
        meta = ffprobe.probe(src)
        recommend.check_caps(meta, config.max_source_seconds)
        safe, reason = recommend.web_safe(meta)
        presets = recommend.recommended_presets(meta.height)
        nxt = transition_status(r, job_id, "awaiting_choice", caller="inspect", extra={
            "source_meta": json.dumps(asdict(meta)),
            "web_safe": "true" if safe else "false",
            "web_safe_reason": reason or "",
            "recommended_presets": json.dumps(presets),
        })
        if nxt is None:
            return {"status": "dropped", "job_id": job_id}
        log.info("inspect_completed", presets=presets, web_safe=safe)
        return {"status": "ok", "job_id": job_id}
    except ffprobe.InspectError as e:
        nxt = transition_status(r, job_id, "failed", caller="inspect", extra={
            "error_code": e.code, "error_message": e.message, "error_stage": "inspect",
        })
        if nxt is None:
            return {"status": "dropped", "job_id": job_id}
        terminal_outbox.drain_one(r, job_id)
        log.error("inspect_failed", code=e.code)
        return {"status": "failed", "error": {"code": e.code, "message": e.message}}
