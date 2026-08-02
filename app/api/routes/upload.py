import shutil
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from kombu.exceptions import OperationalError

from app.api.errors import (
    ErrorResponse,
    InspectionUnavailable,
    InvalidUpload,
    StoragePressure,
    UnsupportedMedia,
    UploadTooLarge,
)
from app.api.utils import new_job_id, now_iso
from app.core.config import config
from app.core.logging import bind_job, get_logger
from app.domain.errors import INSPECT, INSPECTION_UNAVAILABLE
from app.storage import dedupe, paths, terminal_outbox
from app.storage.job_control import atransition_status
from app.storage.pressure import under_pressure
from app.storage.state import get_client
from app.storage.writer import UploadLimitExceeded, stream_to_disk
from app.workers.celery_app import app as celery_app

router = APIRouter(
    tags=["Upload"],
    responses={code: {"model": ErrorResponse} for code in (413, 415, 422, 503)},
)
log = get_logger()

ALLOWED_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}


def _remove_upload_dir(path: Path) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("upload_cleanup_failed", path=str(path))


async def _cleanup(path: Path) -> None:
    await run_in_threadpool(_remove_upload_dir, path)


@router.post("/upload", status_code=202)
async def upload(request: Request, filename: str | None = None):
    if not filename:
        raise InvalidUpload("filename query parameter is required")
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise UnsupportedMedia(f"unsupported extension: {ext}")
    if await run_in_threadpool(under_pressure):
        raise StoragePressure()

    job_id = new_job_id()
    bind_job(job_id)
    dest = config.uploads_dir / job_id / f"source{ext}"
    try:
        content_hash, size = await stream_to_disk(
            request.stream(),
            dest,
            config.max_upload_bytes,
        )
    except UploadLimitExceeded:
        await _cleanup(dest.parent)
        raise UploadTooLarge() from None
    except BaseException:
        await _cleanup(dest.parent)
        raise

    if size == 0:
        await _cleanup(dest.parent)
        raise InvalidUpload("empty upload")

    r = get_client()
    job_fields = {
        "source_filename": filename,
        "content_hash": content_hash,
        "source_path": str(dest),
        "created_at": now_iso(),
    }
    resolution = await dedupe.resolve_upload(
        r,
        content_hash,
        job_id,
        job_fields,
    )
    if resolution.status == "done":
        manifest = paths.output_dir(resolution.job_id) / "manifest.json"
        if await run_in_threadpool(manifest.is_file):
            await r.expire(f"content:{content_hash}", config.output_ttl_days * 86400)
        else:
            await dedupe.invalidate_done(r, content_hash, resolution.job_id)
            resolution = await dedupe.resolve_upload(r, content_hash, job_id, job_fields)
    if resolution.outcome == "hit":
        await _cleanup(dest.parent)
        log.info("upload_completed", dedupe="hit", owner=resolution.job_id)
        return {
            "job_id": resolution.job_id,
            "status": resolution.status,
            "dedupe": "hit",
        }

    status = "inspecting"
    try:
        await run_in_threadpool(
            celery_app.send_task,
            "app.workers.tasks.inspect.probe",
            args=[job_id, str(dest)],
        )
    except OperationalError:
        failed = await atransition_status(
            r,
            job_id,
            "failed",
            caller="upload",
            expected="inspecting",
            extra={
                "error_code": INSPECTION_UNAVAILABLE,
                "error_message": "inspection is temporarily unavailable",
                "error_stage": INSPECT,
            },
        )
        if failed:
            try:
                await terminal_outbox.adrain_one(r, job_id)
            except Exception:
                log.exception("upload_failure_persist_failed")
            finally:
                await _cleanup(dest.parent)
            raise InspectionUnavailable(job_id) from None
        status = await r.hget(f"job:{job_id}", "status") or status
        log.warning("upload_publish_ack_lost", status=status)

    log.info("upload_completed", dedupe="miss", size_bytes=size)
    return {"job_id": job_id, "status": status, "dedupe": "miss"}
