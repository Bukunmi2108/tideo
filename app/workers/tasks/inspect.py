import json
from dataclasses import asdict
from app.core.config import config
from app.domain import recommend
from app.storage.state import get_sync_client
from app.workers import ffprobe
from app.workers.base import InspectTask
from app.workers.celery_app import app
import logging

logger = logging.getLogger(__name__)

@app.task(base=InspectTask)
def probe(job_id: str, src: str) -> dict:
    r = get_sync_client()
    try:
        meta = ffprobe.probe(src)
        recommend.check_caps(meta, config.max_source_seconds)
        safe, reason = recommend.web_safe(meta)
        presets = recommend.recommended_presets(meta.height)
        r.hset(f"job:{job_id}", mapping={
            "status": "awaiting_choice",
            "source_meta": json.dumps(asdict(meta)),
            "web_safe": "true" if safe else "false",
            "web_safe_reason": reason or "",
            "recommended_presets": json.dumps(presets),
        })
        logger.info("inspect ok job=%s presets=%s web_safe=%s", job_id, presets, safe)
        return {"status": "ok", "job_id": job_id}
    except ffprobe.InspectError as e:
        r.hset(f"job:{job_id}", mapping={
            "status": "failed",
            "error_code": e.code, "error_message": e.message, "error_stage": "inspect",
        })
        logger.error("inspect failed job=%s code=%s", job_id, e.code)
        return {"status": "failed", "error": {"code": e.code, "message": e.message}}