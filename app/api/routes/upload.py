import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.errors import InvalidUpload, StoragePressure, UnsupportedMedia, UploadTooLarge
from app.api.utils import new_job_id, now_iso
from app.core.config import config
from app.core.logging import bind_job, get_logger
from app.storage.state import get_client, awrite_status
from app.storage.writer import stream_to_disk
from app.storage.pressure import under_pressure
from app.workers.celery_app import app as celery_app
from app.storage import dedupe

router = APIRouter(tags=["Upload"])
log = get_logger()

ALLOWED_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


@router.post("/upload")
async def upload(request: Request, filename: str | None = None):
    if not filename:
        raise InvalidUpload("filename query parameter is required")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise UnsupportedMedia(f"unsupported extension: {ext}")
    if under_pressure():                                   # shed new work before the disk refuses it disgracefully
        raise StoragePressure()

    job_id = new_job_id()
    bind_job(job_id)
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
    await awrite_status(r, job_id, "inspecting", extra={
        "source_filename": filename,
        "content_hash": content_hash,
        "source_path": str(dest),
        "created_at": now_iso(),
    })

    if await dedupe.claim(r, content_hash, job_id):
        celery_app.send_task("app.workers.tasks.inspect.probe", args=[job_id, str(dest)])
        log.info("upload_completed", dedupe="miss", size_bytes=size)
        return JSONResponse(status_code=202, content={"job_id": job_id, "status": "inspecting", "dedupe": "miss"})

    owner_id = await dedupe.owner(r, content_hash)
    if owner_id and await dedupe.is_valid(r, owner_id):
        shutil.rmtree(dest.parent, ignore_errors=True)
        await r.delete(f"job:{job_id}")
        status = await r.hget(f"job:{owner_id}", "status")
        log.info("upload_completed", dedupe="hit", owner=owner_id)
        return JSONResponse(status_code=202, content={"job_id": owner_id, "status": status, "dedupe": "hit"})

    await dedupe.reclaim(r, content_hash, job_id)

    celery_app.send_task("app.workers.tasks.inspect.probe", args=[job_id, str(dest)])
    log.info("upload_completed", dedupe="miss", size_bytes=size)

    return JSONResponse(
        status_code=202,
        content={"job_id": job_id, "status": "inspecting", "dedupe": "miss"},
    )
