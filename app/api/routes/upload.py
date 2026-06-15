import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.errors import InvalidUpload, UnsupportedMedia, UploadTooLarge
from app.api.utils import new_job_id, now_iso
from app.core.config import config
from app.storage.state import get_client
from app.storage.writer import stream_to_disk
from app.workers.celery_app import app as celery_app

router = APIRouter(tags=["Upload"])

ALLOWED_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


@router.post("/upload")
async def upload(request: Request, filename: str | None = None):
    if not filename:
        raise InvalidUpload("filename query parameter is required")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise UnsupportedMedia(f"unsupported extension: {ext}")

    job_id = new_job_id()
    dest = config.uploads_dir / job_id / f"source{ext}"
    try:
        content_hash, size = await stream_to_disk(request.stream(), dest, config.max_upload_bytes)
    except UploadTooLarge:
        shutil.rmtree(dest.parent, ignore_errors=True)  # no orphan {job_id}/ dir
        raise

    if size == 0:
        shutil.rmtree(dest.parent, ignore_errors=True)
        raise InvalidUpload("empty upload")

    r = get_client()
    await r.hset(f"job:{job_id}", mapping={
        "status": "inspecting",
        "source_filename": filename,
        "content_hash": content_hash,
        "created_at": now_iso(),
    })
    celery_app.send_task("app.workers.tasks.inspect.probe", args=[str(dest)])

    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "status": "inspecting", "dedupe": "miss"},
    )
