import json
import logging
from typing import cast

from celery import chord, group

from app.core.config import config
from app.domain.state import transition
from app.events.producer import emit
from app.events.topics import JOB_FAILED
from app.storage.state import get_sync_client
from app.workers.celery_app import app as celery_app

logger = logging.getLogger(__name__)

RENDITION = "app.workers.tasks.rendition.rendition"
PACKAGE = "app.workers.tasks.package.package"
THUMBS = "app.workers.tasks.thumbs.thumbs"


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
        + [celery_app.signature(THUMBS, args=[job_id, src, meta]).set(link_error=err)]  # poster+sprite, in parallel
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
    r = get_sync_client()
    cur = cast(str, r.hget(f"job:{job_id}", "status")) or ""
    nxt = transition(cur, "failed", job_id=job_id, caller="chord-fail")
    if nxt:                                                   # None -> already terminal, drop
        r.hset(f"job:{job_id}", mapping={
            "status": nxt, "error_code": "ENCODE_FAILED", "error_stage": "transcode",
        })
        emit(JOB_FAILED, job_id, {"error_code": "ENCODE_FAILED", "stage": "transcode"})
        r.publish(f"progress:{job_id}", json.dumps({"event": "terminal"}))  # wake a live WS relay
    for tid in json.loads(r.hget(f"job:{job_id}", "rendition_ids") or "[]"):
        celery_app.control.revoke(tid, terminate=True)        # best-effort sibling revocation
