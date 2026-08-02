import json
from datetime import UTC, datetime
from typing import cast

from celery import chord, group
from celery.utils import uuid

from app.core.config import config
from app.core.logging import bind_job, get_logger
from app.domain.errors import ENCODE_FAILED_TRANSIENT, TRANSCODE
from app.domain.errors import PACKAGE as PACKAGE_STAGE
from app.events.producer import emit
from app.events.topics import JOB_FAILED
from app.storage.db import persist_terminal
from app.storage.job_control import (
    DispatchPlan,
    reserve_dispatch,
    transition_status,
)
from app.storage.state import get_sync_client
from app.workers import dlq
from app.workers.celery_app import app as celery_app
from app.workers.source import claim_source, release_source

log = get_logger()

RENDITION = "app.workers.tasks.rendition.rendition"
PACKAGE = "app.workers.tasks.package.package"
TRANSCRIBE = "app.workers.tasks.transcribe.transcribe"


def _unique_presets(presets: list[str]) -> list[str]:
    """Deduplicate and cap presets."""
    unique = list(dict.fromkeys(presets))
    if len(unique) != len(presets):
        log.warning("duplicate_presets_normalized", presets=presets)
    return unique[: config.dev_max_renditions]


def _new_plan(event_id: str, presets: list[str], subtitles: bool) -> DispatchPlan:
    return DispatchPlan(
        event_id=event_id,
        rendition_ids=tuple(uuid() for _ in presets),
        callback_id=uuid(),
        transcribe_id=uuid() if subtitles else None,
    )


def dispatch_job(job_id: str, event_id: str, presets: list[str], subtitles: bool) -> str:
    """Reserve task IDs and submit a job."""
    r = get_sync_client()
    rec = r.hgetall(f"job:{job_id}")
    if not rec:
        log.warning("dispatch_skipped", reason="job_missing")
        return "skipped"

    presets = _unique_presets(presets)
    proposed = _new_plan(event_id, presets, subtitles)
    reservation = reserve_dispatch(r, job_id, proposed)
    if reservation.outcome in ("missing", "skipped", "conflict") or reservation.plan is None:
        log.info("dispatch_skipped", reason=reservation.outcome, status=reservation.status)
        return "skipped"
    plan = reservation.plan
    if len(plan.rendition_ids) != len(presets):
        raise RuntimeError(f"dispatch plan/preset mismatch for job {job_id}")

    src = rec["source_path"]
    meta = json.loads(rec["source_meta"])
    err = fail_job.s(job_id)  # type: ignore[reportFunctionMemberAccess]
    header = group([
        celery_app.signature(RENDITION, args=[job_id, preset, src, meta]).set(
            task_id=task_id,
            link_error=err,
        )
        for preset, task_id in zip(presets, plan.rendition_ids, strict=True)
    ])
    callback = celery_app.signature(PACKAGE, args=[job_id]).set(
        task_id=plan.callback_id,
        link_error=err,
    )

    claim_source(r, job_id, "package")
    chord(header)(callback)
    log.info("job_dispatched", presets=presets, callback_id=plan.callback_id)

    if plan.transcribe_id:
        try:
            if r.exists(f"cancel:{job_id}") or r.hget(f"job:{job_id}", "status") == "cancelled":
                log.info("transcribe_dispatch_skipped", reason="job_cancelled")
                return "dispatched"
            r.hset(f"job:{job_id}", mapping={"subtitles": json.dumps({"status": "processing"})})
            claim_source(r, job_id, "transcribe")
            celery_app.signature(TRANSCRIBE, args=[job_id, src, meta]).set(
                task_id=plan.transcribe_id,
            ).apply_async()
            log.info("transcribe_dispatched", task_id=plan.transcribe_id)
        except Exception:
            payload = {"status": "failed", "code": "STT_DISPATCH_FAILED", "reason": "could not enqueue captions"}
            try:
                r.hset(f"job:{job_id}", mapping={"subtitles": json.dumps(payload)})
            except Exception:
                log.exception("transcribe_dispatch_status_failed")
            try:
                release_source(r, job_id, "transcribe")
            except Exception:
                log.exception("transcribe_source_release_failed")
            log.exception("transcribe_dispatch_failed")
    return "dispatched"


@celery_app.task(name="app.dispatcher.dispatch.fail_job")
def fail_job(request, exc, traceback, job_id: str):
    """Handle a hard chord failure."""
    bind_job(job_id)
    r = get_sync_client()
    fallback_stage = PACKAGE_STAGE if getattr(request, "task", None) == PACKAGE else TRANSCODE
    fallback_message = "package failed" if fallback_stage == PACKAGE_STAGE else "transcoding failed"
    nxt = transition_status(r, job_id, "failed", caller="chord-fail", extra={
        "error_code": cast(str, r.hget(f"job:{job_id}", "error_code")) or ENCODE_FAILED_TRANSIENT,
        "error_message": cast(str, r.hget(f"job:{job_id}", "error_message")) or fallback_message,
        "error_stage": cast(str, r.hget(f"job:{job_id}", "error_stage")) or fallback_stage,
    })
    if nxt:                                                   # None -> already terminal, drop
        code = cast(str, r.hget(f"job:{job_id}", "error_code")) or ENCODE_FAILED_TRANSIENT
        msg = cast(str, r.hget(f"job:{job_id}", "error_message")) or fallback_message
        stage = cast(str, r.hget(f"job:{job_id}", "error_stage")) or fallback_stage
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
            "failed_at": datetime.now(UTC).isoformat(),
        })
    for tid in json.loads(r.hget(f"job:{job_id}", "rendition_ids") or "[]"):
        celery_app.control.revoke(tid, terminate=True)        # best-effort sibling revocation
