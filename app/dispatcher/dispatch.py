import json
import logging
from datetime import datetime, timezone
from typing import cast

from celery import chord, group

from app.core.config import config
from app.core.logging import bind_job
from app.domain.errors import ENCODE_FAILED_TRANSIENT
from app.domain.state import transition
from app.events.producer import emit
from app.events.topics import JOB_FAILED
from app.storage.db import persist_terminal
from app.storage.state import get_sync_client
from app.workers import dlq
from app.workers.celery_app import app as celery_app

logger = logging.getLogger(__name__)

RENDITION = "app.workers.tasks.rendition.rendition"
PACKAGE = "app.workers.tasks.package.package"


def build_and_fire_chord(job_id: str, presets: list[str]) -> None:
    """Turn one job.created into the parallel ladder: a chord of N rendition tasks (header) joined
    by the packaging callback (body). Records task-ids for Phase-6 cancel, then fires."""
    r = get_sync_client()
    rec = r.hgetall(f"job:{job_id}")
    src = rec["source_path"]
    meta = json.loads(rec["source_meta"])
    presets = presets[: config.dev_max_renditions]

    err = fail_job.s(job_id)  # type: ignore[reportFunctionMemberAccess]  # celery Task .s(); stubs see a func
    header = group(
        [celery_app.signature(RENDITION, args=[job_id, p, src, meta]).set(link_error=err)
         for p in presets]
    )
    callback = celery_app.signature(PACKAGE, args=[job_id]).set(link_error=err)
    result = chord(header)(callback)

    header_ids = [c.id for c in (result.parent.children or [])] if result.parent else []
    r.hset(f"job:{job_id}", mapping={
        "chord_callback_id": result.id,
        "rendition_ids": json.dumps(header_ids),
    })
    logger.info("dispatched chord job=%s presets=%s callback=%s", job_id, presets, result.id)


@celery_app.task(name="app.dispatcher.dispatch.fail_job")
def fail_job(request, exc, traceback, job_id: str):
    """link_error handler — runs on any header/callback hard failure. ADR-3: the whole job fails."""
    bind_job(job_id)
    r = get_sync_client()
    cur = cast(str, r.hget(f"job:{job_id}", "status")) or ""
    nxt = transition(cur, "failed", job_id=job_id, caller="chord-fail")
    if nxt:                                                   # None -> already terminal, drop
        code = cast(str, r.hget(f"job:{job_id}", "error_code")) or ENCODE_FAILED_TRANSIENT
        msg = cast(str, r.hget(f"job:{job_id}", "error_message")) or "transcoding failed"
        stage = cast(str, r.hget(f"job:{job_id}", "error_stage")) or "transcode"
        r.hset(f"job:{job_id}", mapping={
            "status": nxt, "error_code": code, "error_message": msg, "error_stage": stage,
        })
        r.expire(f"job:{job_id}", config.output_ttl_days * 86400)
        persist_terminal(job_id, r.hgetall(f"job:{job_id}"))
        emit(JOB_FAILED, job_id, {"error_code": code, "stage": stage})
        r.publish(f"progress:{job_id}", json.dumps({"event": "terminal"}))  # wake a live WS relay
        dlq.add(r, {
            "id": getattr(request, "id", None) or job_id,
            "task": getattr(request, "task", None) or "unknown",
            "args": list(getattr(request, "args", None) or []),
            "error_code": code, "error_message": msg, "error_stage": stage,
            "stderr": cast(str, r.hget(f"job:{job_id}", "error_stderr")) or "",
            "attempts": (getattr(request, "retries", 0) or 0) + 1,
            "job_id": job_id,
            "failed_at": datetime.now(timezone.utc).isoformat(),
        })
    for tid in json.loads(r.hget(f"job:{job_id}", "rendition_ids") or "[]"):
        celery_app.control.revoke(tid, terminate=True)        # best-effort sibling revocation
