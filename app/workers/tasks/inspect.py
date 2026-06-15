from dataclasses import asdict
import logging
from app.workers import ffprobe
from app.workers.base import InspectTask
from app.workers.celery_app import app

logger = logging.getLogger(__name__)

@app.task(base=InspectTask)
def probe(path: str) -> dict:
    try:
        meta = ffprobe.probe(path)
        logger.info("inspect ok path=%s meta=%s", path, asdict(meta))
        return {"status": "ok", "meta": asdict(meta)}
    except ffprobe.InspectError as e:
        logger.error("inspect failed path=%s code=%s msg=%s", path, e.code, e.message)
        return {"status": "failed", "error": {"code": e.code, "message": e.message}}