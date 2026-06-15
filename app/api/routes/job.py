from fastapi import APIRouter
from app.api.model import JobResponse
from app.storage.state import get_client
from app.api.errors import ApiError
import json

router = APIRouter(tags=["Job"])

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