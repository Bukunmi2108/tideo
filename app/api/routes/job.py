import json
import math
import shutil
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.api.errors import ApiError, ErrorResponse, StoragePressure
from app.api.model import (
    JobListResponse,
    JobResponse,
    error_view,
    json_list,
    progress_map,
    results_view,
    results_view_pg,
)
from app.api.session import owns_job, require_session, session_jobs_key
from app.core.config import config
from app.core.logging import bind_job, get_logger
from app.domain.state import ACTIVE, TERMINAL
from app.events.envelope import Envelope
from app.events.producer import emit
from app.events.topics import JOB_CANCELLED, JOB_CREATED
from app.storage import terminal_outbox
from app.storage.db import get_job as db_get_job
from app.storage.db import list_jobs as db_list_jobs
from app.storage.job_control import acancel_job, aqueue_job
from app.storage.pressure import under_pressure
from app.storage.state import get_client
from app.workers.celery_app import app as celery_app

router = APIRouter(
    tags=["Job"],
    responses={code: {"model": ErrorResponse} for code in (401, 404, 409, 410, 422, 503)},
)
log = get_logger()


class TranscodeRequest(BaseModel):
    presets: list[str]
    subtitles: bool = False


def _duplicates(values: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt if isinstance(dt, str) else dt.isoformat()


def _datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _job_summary(row: dict) -> dict:
    """Build a history summary."""
    job_id, status, finished = row["job_id"], row["status"], row.get("finished_at")
    duration = row.get("source_duration_s")
    poster_avail = status == "done" and (config.output_dir / job_id / "poster.jpg").exists()
    expires_at = (finished + timedelta(days=config.output_ttl_days)) if status == "done" and finished else None
    return {
        "job_id": job_id,
        "status": status,
        "source_filename": row.get("source_filename"),
        "duration": float(duration) if duration is not None else None,
        "created_at": _iso(row.get("created_at")),
        "finished_at": _iso(finished),
        "expires_at": _iso(expires_at),
        "poster": f"/jobs/{job_id}/poster" if poster_avail else None,
    }


def _redis_job_summary(job_id: str, rec: dict) -> dict:
    source = {}
    try:
        source = json.loads(rec.get("source_meta") or "{}")
    except (json.JSONDecodeError, TypeError):
        pass
    if not isinstance(source, dict):
        source = {}
    duration = source.get("duration")
    if isinstance(duration, bool):
        duration = None
    else:
        try:
            duration = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None
    if duration is not None and not math.isfinite(duration):
        duration = None
    finished = _datetime(rec.get("finished_at"))
    status = rec.get("status", "")
    row = {
        "job_id": job_id,
        "status": status,
        "source_filename": rec.get("source_filename"),
        "source_duration_s": duration,
        "created_at": _datetime(rec.get("created_at")),
        "finished_at": finished,
    }
    return _job_summary(row)


def _not_found(job_id: str) -> ApiError:
    return ApiError(404, "JOB_NOT_FOUND", "no such job", job_id=job_id)


def _require_owner(record: dict | None, owner_session_hash: str, job_id: str) -> dict:
    if not owns_job(record, owner_session_hash):
        raise _not_found(job_id)
    return record or {}


def _response_from_pg(job_id: str, row: dict) -> dict:
    """Build a job response from Postgres."""
    status = row["status"]
    if status == "expired":
        raise ApiError(410, "JOB_EXPIRED", "job outputs have expired", job_id=job_id)
    resp = {"job_id": job_id, "status": status, "source_filename": row.get("source_filename")}
    if status == "done":
        resp["results"] = results_view_pg(job_id, row)
    elif status == "failed":
        resp["error"] = error_view(row)
    return resp


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    owner_session_hash: Annotated[str, Depends(require_session)],
    status: str | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
):
    if status is not None and status not in ACTIVE | TERMINAL:
        raise ApiError(422, "BAD_STATUS", f"unknown status filter: {status}")

    r = get_client()
    ids = await r.zrevrange(session_jobs_key(owner_session_hash), 0, -1)
    items_by_id = {}
    stale = []
    for job_id in ids:
        rec = await r.hgetall(f"job:{job_id}")
        if not rec:
            stale.append(job_id)
            continue
        if not owns_job(rec, owner_session_hash):
            stale.append(job_id)
            continue
        if status is None or rec.get("status") == status:
            items_by_id[job_id] = _redis_job_summary(job_id, rec)
    if stale:
        await r.zrem(session_jobs_key(owner_session_hash), *stale)

    target = offset + limit + 1
    rows = []
    if status is None or status in TERMINAL:
        rows = await run_in_threadpool(
            db_list_jobs,
            owner_session_hash=owner_session_hash,
            status=status,
            limit=target,
            offset=0,
        )
    for row in rows:
        items_by_id[row["job_id"]] = _job_summary(row)
    items = sorted(
        items_by_id.values(),
        key=lambda item: (item.get("created_at") or "", item["job_id"]),
        reverse=True,
    )
    page = items[offset : offset + limit]
    return {
        "items": page,
        "limit": limit,
        "offset": offset,
        "has_more": len(items) > offset + limit,
    }


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: str,
    owner_session_hash: Annotated[str, Depends(require_session)],
):
    bind_job(job_id)
    rec = await get_client().hgetall(f"job:{job_id}")
    if not rec or "status" not in rec:
        row = await run_in_threadpool(db_get_job, job_id)
        _require_owner(row, owner_session_hash, job_id)
        return _response_from_pg(job_id, row)
    _require_owner(rec, owner_session_hash, job_id)
    status = rec["status"]
    if status == "expired":
        raise ApiError(410, "JOB_EXPIRED", "job outputs have expired", job_id=job_id)

    resp = {"job_id": job_id, "status": status, "source_filename": rec.get("source_filename") or None}
    if status == "awaiting_choice":
        resp["source"] = json.loads(rec["source_meta"])
        resp["recommended_presets"] = json_list(rec, "recommended_presets", job_id)
        resp["web_safe"] = rec["web_safe"] == "true"
        resp["web_safe_reason"] = rec.get("web_safe_reason") or None
    elif status in ("queued", "transcoding"):
        resp["progress"] = progress_map(rec, job_id)
        resp["presets"] = json_list(rec, "presets", job_id)
    elif status == "done":
        resp["results"] = results_view(job_id, rec)
    elif status == "failed":
        resp["error"] = error_view(rec)
    return resp

@router.post("/jobs/{job_id}/transcode", status_code=202)
async def transcode(
    job_id: str,
    body: TranscodeRequest,
    owner_session_hash: Annotated[str, Depends(require_session)],
):
    bind_job(job_id)
    r = get_client()
    rec = await r.hgetall(f"job:{job_id}")
    _require_owner(rec, owner_session_hash, job_id)
    if rec["status"] != "awaiting_choice":
        raise ApiError(409, "WRONG_STATE",
                       f"job is {rec['status']}, not awaiting_choice", job_id=job_id)

    recommended = json.loads(rec["recommended_presets"])
    duplicates = _duplicates(body.presets)
    if duplicates:
        raise ApiError(422, "DUPLICATE_PRESET",
                       f"duplicate presets: {duplicates}", job_id=job_id)
    bad = [p for p in body.presets if p not in recommended]
    if not body.presets or bad:
        raise ApiError(422, "PRESET_NOT_RECOMMENDED",
                       f"presets not in recommendation: {bad or body.presets}", job_id=job_id)
    if await run_in_threadpool(under_pressure):
        raise StoragePressure(job_id)

    duration = json.loads(rec["source_meta"]).get("duration")
    if (
        isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise ApiError(
            409,
            "INVALID_SOURCE_METADATA",
            "source duration is missing or invalid",
            job_id=job_id,
        )
    event = Envelope(JOB_CREATED, job_id, {
        "presets": body.presets, "subtitles": body.subtitles, "source_duration": duration,
    })
    nxt = await aqueue_job(
        r,
        job_id,
        presets=json.dumps(body.presets),
        subtitles=body.subtitles,
        event_id=event.event_id,
        event_json=event.to_json(),
    )
    if nxt is None:
        current = await r.hget(f"job:{job_id}", "status")
        raise ApiError(409, "WRONG_STATE", f"job is {current}, not awaiting_choice", job_id=job_id)
    return {"job_id": job_id, "status": nxt}


@router.post("/jobs/{job_id}/cancel", status_code=202)
async def cancel(
    job_id: str,
    owner_session_hash: Annotated[str, Depends(require_session)],
):
    bind_job(job_id)
    r = get_client()
    rec = await r.hgetall(f"job:{job_id}")
    _require_owner(rec, owner_session_hash, job_id)
    result = await acancel_job(r, job_id, ttl=config.output_ttl_days * 86400)
    if result.outcome == "missing":
        raise ApiError(404, "JOB_NOT_FOUND", "no such job", job_id=job_id)
    if result.outcome == "wrong_state":
        raise ApiError(409, "WRONG_STATE",
                       f"job is {result.previous_status}, not cancellable", job_id=job_id)

    try:
        await terminal_outbox.adrain_one(r, job_id)
    except Exception:
        log.exception("cancel_persist_failed")

    if result.task_ids:
        try:
            celery_app.control.revoke(list(result.task_ids))
        except Exception:
            log.exception("cancel_revoke_failed", task_count=len(result.task_ids))

    emit(JOB_CANCELLED, job_id, {})
    try:
        await r.publish(f"progress:{job_id}", json.dumps({"event": "terminal"}))
    except Exception:
        log.exception("cancel_ws_wake_failed")
    await run_in_threadpool(_remove_cancelled_outputs, job_id)
    return {"job_id": job_id, "status": "cancelled"}


def _remove_cancelled_outputs(job_id: str) -> None:
    try:
        shutil.rmtree(config.output_dir / job_id)
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("cancel_output_cleanup_failed")
