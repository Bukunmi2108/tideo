from fastapi import APIRouter
from app.api.model import JobResponse
from app.storage.state import get_client
from app.api.errors import ApiError
import json
from pydantic import BaseModel
from app.domain.state import transition
from app.events.envelope import Envelope
from app.events.producer import publish
from app.events.topics import JOB_CREATED


router = APIRouter(tags=["Job"])

class TranscodeRequest(BaseModel):
    presets: list[str]
    subtitles: bool = False


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: str):
    rec = await get_client().hgetall(f"job:{job_id}")
    if not rec:
        raise ApiError(404, "JOB_NOT_FOUND", "no such job", job_id=job_id)
    status = rec["status"]
    if status == "expired":
        raise ApiError(410, "JOB_EXPIRED", "job outputs have expired", job_id=job_id)

    resp = {"job_id": job_id, "status": status}
    if status == "awaiting_choice":
        resp["source"] = json.loads(rec["source_meta"])
        resp["recommended_presets"] = json.loads(rec["recommended_presets"])
        resp["web_safe"] = rec["web_safe"] == "true"
        resp["web_safe_reason"] = rec.get("web_safe_reason") or None
    elif status in ("queued", "transcoding"):
        resp["progress"] = {}
    elif status == "done":
        resp["results"] = {}
    elif status == "failed":
        resp["error"] = {
            "code": rec.get("error_code"), "message": rec.get("error_message"),
            "stage": rec.get("error_stage"), "retryable": False,
        }
    return resp

@router.post("/jobs/{job_id}/transcode", status_code=202)
async def transcode(job_id: str, body: TranscodeRequest):
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