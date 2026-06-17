import json
from dataclasses import asdict
from app.core.config import config
from app.domain import recommend
from app.domain.state import transition
from app.core.logging import bind_job
from app.storage.db import persist_terminal
from app.storage.state import get_sync_client
from app.workers import ffprobe
from app.workers.base import InspectTask
from app.workers.celery_app import app
import logging
from typing import cast

logger = logging.getLogger(__name__)


def _current_status(r, job_id: str) -> str:
    return cast(str, r.hget(f"job:{job_id}", "status") or "inspecting")


@app.task(base=InspectTask)
def probe(job_id: str, src: str) -> dict:
    bind_job(job_id)
    r = get_sync_client()
    try:
        meta = ffprobe.probe(src)
        recommend.check_caps(meta, config.max_source_seconds)
        safe, reason = recommend.web_safe(meta)
        presets = recommend.recommended_presets(meta.height)
        cur = _current_status(r, job_id)
        nxt = transition(cur, "awaiting_choice", job_id=job_id, caller="inspect")
        if nxt is None:
            return {"status": "dropped", "job_id": job_id}
        r.hset(f"job:{job_id}", mapping={
            "status": nxt,
            "source_meta": json.dumps(asdict(meta)),
            "web_safe": "true" if safe else "false",
            "web_safe_reason": reason or "",
            "recommended_presets": json.dumps(presets),
        })
        logger.info("inspect ok job=%s presets=%s web_safe=%s", job_id, presets, safe)
        return {"status": "ok", "job_id": job_id}
    except ffprobe.InspectError as e:
        cur = _current_status(r, job_id)
        nxt = transition(cur, "failed", job_id=job_id, caller="inspect")
        if nxt is None:
            return {"status": "dropped", "job_id": job_id}
        r.hset(f"job:{job_id}", mapping={
            "status": nxt,
            "error_code": e.code, "error_message": e.message, "error_stage": "inspect",
        })
        r.expire(f"job:{job_id}", config.output_ttl_days * 86400)
        persist_terminal(job_id, r.hgetall(f"job:{job_id}"))
        logger.error("inspect failed job=%s code=%s", job_id, e.code)
        return {"status": "failed", "error": {"code": e.code, "message": e.message}}