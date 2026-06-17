import json
import shutil
from datetime import timedelta
from fastapi import APIRouter, Query
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from app.api.model import (
    JobListResponse, JobResponse, progress_map, results_view, results_view_pg,
)
from app.core.config import config
from app.core.logging import bind_job
from app.storage.state import get_client
from app.storage.db import persist_terminal, get_job as db_get_job, list_jobs as db_list_jobs
from app.storage.pressure import under_pressure
from app.storage import paths
from app.api.errors import ApiError, StoragePressure
from app.domain.state import transition
from app.events.envelope import Envelope
from app.events.producer import publish
from app.events.topics import JOB_CREATED, JOB_CANCELLED
from app.workers.celery_app import app as celery_app


router = APIRouter(tags=["Job"])

TERMINAL_STATUSES = ("done", "failed", "cancelled", "expired")


class TranscodeRequest(BaseModel):
    presets: list[str]
    subtitles: bool = False


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def _job_summary(row: dict) -> dict:
    """Cold Postgres row -> history-card shape."""
    job_id, status, finished = row["job_id"], row["status"], row.get("finished_at")
    duration = row.get("source_duration_s")
    poster_avail = status == "done" and (config.output_dir / job_id / "poster.jpg").exists()
    # the 7.2 sweep deletes done jobs at finished_at + OUTPUT_TTL_DAYS — that's the countdown anchor
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


def _response_from_pg(job_id: str, row: dict) -> dict:
    """GET /jobs/{id} response from the cold tier when the hot Redis hash is gone."""
    status = row["status"]
    if status == "expired":
        raise ApiError(410, "JOB_EXPIRED", "job outputs have expired", job_id=job_id)
    resp = {"job_id": job_id, "status": status, "source_filename": row.get("source_filename")}
    if status == "done":
        resp["results"] = results_view_pg(job_id, row)
    elif status == "failed":
        resp["error"] = {
            "code": row.get("error_code"), "message": row.get("error_message"),
            "stage": row.get("error_stage"), "retryable": False,
        }
    return resp                                              # cancelled: status only, like the hot path


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    status: str | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
):
    if status is not None and status not in TERMINAL_STATUSES:
        raise ApiError(422, "BAD_STATUS", f"unknown status filter: {status}")
    rows = await run_in_threadpool(db_list_jobs, status=status, limit=limit, offset=offset)
    has_more = len(rows) > limit
    return {
        "items": [_job_summary(r) for r in rows[:limit]],
        "limit": limit, "offset": offset, "has_more": has_more,
    }


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    bind_job(job_id)
    rec = await get_client().hgetall(f"job:{job_id}")
    if not rec or "status" not in rec:                   # hot state gone/torn -> fall back to the cold tier
        row = await run_in_threadpool(db_get_job, job_id)
        if not row:
            raise ApiError(404, "JOB_NOT_FOUND", "no such job", job_id=job_id)
        return _response_from_pg(job_id, row)
    status = rec["status"]
    if status == "expired":
        raise ApiError(410, "JOB_EXPIRED", "job outputs have expired", job_id=job_id)

    resp = {"job_id": job_id, "status": status}
    if status == "awaiting_choice":
        resp["source"] = json.loads(rec["source_meta"])
        resp["source_filename"] = rec.get("source_filename") or None
        resp["recommended_presets"] = json.loads(rec["recommended_presets"])
        resp["web_safe"] = rec["web_safe"] == "true"
        resp["web_safe_reason"] = rec.get("web_safe_reason") or None
    elif status in ("queued", "transcoding"):
        resp["progress"] = progress_map(rec)
        resp["presets"] = json.loads(rec["presets"]) if rec.get("presets") else []
    elif status == "done":
        resp["results"] = results_view(job_id, rec)
    elif status == "failed":
        resp["error"] = {
            "code": rec.get("error_code"), "message": rec.get("error_message"),
            "stage": rec.get("error_stage"), "retryable": False,
        }
    return resp

@router.post("/jobs/{job_id}/transcode", status_code=202)
async def transcode(job_id: str, body: TranscodeRequest):
    bind_job(job_id)
    r = get_client()
    rec = await r.hgetall(f"job:{job_id}")
    if not rec:
        raise ApiError(404, "JOB_NOT_FOUND", "no such job", job_id=job_id)
    if rec["status"] != "awaiting_choice":
        raise ApiError(409, "WRONG_STATE",
                       f"job is {rec['status']}, not awaiting_choice", job_id=job_id)

    recommended = json.loads(rec["recommended_presets"])
    bad = [p for p in body.presets if p not in recommended]
    if not body.presets or bad:
        raise ApiError(422, "PRESET_NOT_RECOMMENDED",
                       f"presets not in recommendation: {bad or body.presets}", job_id=job_id)
    if under_pressure():                                   # gate new encode work; in-flight jobs are untouched
        raise StoragePressure()

    nxt = transition("awaiting_choice", "queued", job_id=job_id, caller="transcode")
    assert nxt is not None
    await r.hset(f"job:{job_id}", mapping={
        "status": nxt,
        "presets": json.dumps(body.presets),
        "subtitles": "true" if body.subtitles else "false",
    })

    duration = json.loads(rec["source_meta"]).get("duration")
    publish(Envelope(JOB_CREATED, job_id, {
        "presets": body.presets, "subtitles": body.subtitles, "source_duration": duration,
    }))
    return {"job_id": job_id, "status": nxt}


@router.post("/jobs/{job_id}/cancel", status_code=202)
async def cancel(job_id: str):
    bind_job(job_id)
    r = get_client()
    rec = await r.hgetall(f"job:{job_id}")
    if not rec:
        raise ApiError(404, "JOB_NOT_FOUND", "no such job", job_id=job_id)
    if rec["status"] not in ("queued", "transcoding"):
        raise ApiError(409, "WRONG_STATE", f"job is {rec['status']}, not cancellable", job_id=job_id)

    nxt = transition(rec["status"], "cancelled", job_id=job_id, caller="cancel")
    assert nxt is not None
    await r.set(f"cancel:{job_id}", "1", ex=3600)          # flag the running encode loop to kill FFmpeg
    await r.hset(f"job:{job_id}", mapping={"status": nxt})
    await r.expire(f"job:{job_id}", config.output_ttl_days * 86400)
    await run_in_threadpool(persist_terminal, job_id, await r.hgetall(f"job:{job_id}"))

    ids = json.loads(rec.get("rendition_ids") or "[]")
    if rec.get("chord_callback_id"):
        ids.append(rec["chord_callback_id"])
    if ids:
        celery_app.control.revoke(ids)

    publish(Envelope(JOB_CANCELLED, job_id, {}))
    await r.publish(f"progress:{job_id}", json.dumps({"event": "terminal"}))  # wake a live WS relay
    shutil.rmtree(paths.output_dir(job_id), ignore_errors=True)               # job won't ship; drop partials
    return {"job_id": job_id, "status": nxt}