import json
import re
from pathlib import Path
from typing import cast

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.api.errors import ApiError
from app.core.logging import get_logger
from app.domain.ladder import PRESETS
from app.storage import paths
from app.storage.db import get_job as db_get_job
from app.storage.state import get_client

router = APIRouter(tags=["Artifacts"])
log = get_logger()
_SEG = re.compile(r"^seg_\d{5}\.ts$")

HLS_MIME  = "application/vnd.apple.mpegurl"
TS_MIME   = "video/mp2t"
MP4_MIME  = "video/mp4"
JPEG_MIME = "image/jpeg"
VTT_MIME  = "text/vtt"

NO_CACHE  = "no-cache"
IMMUTABLE = "max-age=31536000, immutable"
SHORT     = "max-age=3600"


async def _resolve_status(job_id: str) -> str | None:
    """Resolve status from Redis, then Postgres."""
    status = cast(str | None, await get_client().hget(f"job:{job_id}", "status"))
    if status is not None:
        return status
    row = await run_in_threadpool(db_get_job, job_id)
    return row["status"] if row else None


async def _guard(job_id: str) -> Path:
    """Require a completed, unexpired job."""
    status = await _resolve_status(job_id)
    if status is None:
        raise ApiError(404, "NOT_FOUND", "job not found", job_id)
    if status == "expired":
        raise ApiError(410, "EXPIRED", "job artifacts have expired", job_id)
    if status != "done":
        raise ApiError(404, "NOT_READY", "artifacts not yet available", job_id)
    return paths.output_dir(job_id)


def _safe(job_dir: Path, *parts: str) -> Path:
    """Keep resolved paths inside the job directory."""
    p = Path(job_dir, *parts).resolve()
    if not p.is_relative_to(job_dir.resolve()):
        raise ApiError(403, "FORBIDDEN", "invalid path", "")
    return p


def _require_artifact(path: Path, job_id: str, artifact: str) -> Path:
    """Require an artifact file."""
    if path.is_file():
        return path
    log.warning("artifact_missing", job_id=job_id, artifact=artifact)
    raise ApiError(404, "ARTIFACT_MISSING", "artifact is unavailable", job_id)


@router.get("/jobs/{job_id}/playlist")
async def master_playlist(job_id: str):
    job_dir = await _guard(job_id)
    path = _require_artifact(job_dir / "master.m3u8", job_id, "master_playlist")
    content = path.read_text()
    return Response(content, media_type=HLS_MIME, headers={"Cache-Control": NO_CACHE})


@router.get("/jobs/{job_id}/playlist/subs")
async def subtitle_playlist(job_id: str):
    job_dir = await _guard(job_id)
    path = job_dir / "subs.m3u8"
    if not path.is_file():
        raise ApiError(404, "NOT_READY", "subtitles not available", job_id)
    return Response(path.read_text(), media_type=HLS_MIME, headers={"Cache-Control": NO_CACHE})


@router.get("/jobs/{job_id}/manifest")
async def manifest(job_id: str):
    """Serve the job manifest."""
    job_dir = await _guard(job_id)
    path = job_dir / "manifest.json"
    path = _require_artifact(path, job_id, "manifest")
    return Response(path.read_text(), media_type="application/json", headers={"Cache-Control": SHORT})


@router.get("/jobs/{job_id}/storyboard")
async def storyboard(job_id: str):
    """Serve storyboard geometry."""
    job_dir = await _guard(job_id)
    path = job_dir / "manifest.json"
    if not path.is_file():
        raise ApiError(404, "NOT_READY", "storyboard not available", job_id)
    sb = json.loads(path.read_text()).get("storyboard")
    if not sb:
        raise ApiError(404, "NOT_READY", "storyboard not available", job_id)
    return Response(json.dumps(sb), media_type="application/json", headers={"Cache-Control": SHORT})


@router.get("/jobs/{job_id}/subtitles")
async def subtitles(job_id: str):
    job_dir = await _guard(job_id)
    path = job_dir / "subtitles.vtt"
    if not path.is_file():
        raise ApiError(404, "NOT_READY", "subtitles not available", job_id)
    return FileResponse(str(path), media_type=VTT_MIME, headers={"Cache-Control": NO_CACHE})


@router.get("/jobs/{job_id}/playlist/{preset}")
async def rendition_playlist(job_id: str, preset: str):
    if preset not in PRESETS:
        raise ApiError(404, "NOT_FOUND", "unknown preset", job_id)
    job_dir = await _guard(job_id)
    src = _safe(job_dir, preset, "index.m3u8")
    src = _require_artifact(src, job_id, f"rendition_playlist:{preset}")
    content = src.read_text()
    content = re.sub(r"(seg_\d{5}\.ts)", rf"../segments/{preset}/\1", content)
    return Response(content, media_type=HLS_MIME, headers={"Cache-Control": NO_CACHE})


@router.get("/jobs/{job_id}/segments/{preset}/{filename}")
async def segment(job_id: str, preset: str, filename: str):
    if preset not in PRESETS:
        raise ApiError(404, "NOT_FOUND", "unknown preset", job_id)
    if not _SEG.match(filename):
        raise ApiError(404, "NOT_FOUND", "invalid segment name", job_id)
    job_dir = await _guard(job_id)
    path = _safe(job_dir, preset, filename)
    path = _require_artifact(path, job_id, f"segment:{preset}:{filename}")
    return FileResponse(str(path), media_type=TS_MIME,
                        headers={"Cache-Control": IMMUTABLE})


@router.get("/jobs/{job_id}/file")
async def web_mp4(job_id: str):
    job_dir = await _guard(job_id)
    path = _require_artifact(job_dir / "web.mp4", job_id, "web_mp4")
    return FileResponse(str(path), media_type=MP4_MIME,
                        headers={"Cache-Control": SHORT})


@router.get("/jobs/{job_id}/poster")
async def poster(job_id: str):
    job_dir = await _guard(job_id)
    path = _require_artifact(job_dir / "poster.jpg", job_id, "poster")
    return FileResponse(str(path), media_type=JPEG_MIME, headers={"Cache-Control": SHORT})


@router.get("/jobs/{job_id}/sprite")
async def sprite(job_id: str):
    job_dir = await _guard(job_id)
    path = _require_artifact(job_dir / "sprite.jpg", job_id, "sprite")
    return FileResponse(str(path), media_type=JPEG_MIME, headers={"Cache-Control": SHORT})


@router.get("/jobs/{job_id}/player")
async def player(job_id: str):
    job_dir = await _guard(job_id)
    path = _require_artifact(job_dir / "embed.html", job_id, "player")
    return HTMLResponse(path.read_text(),
                        headers={"Cache-Control": SHORT})
