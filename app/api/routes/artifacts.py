import json
import re
from pathlib import Path
from typing import cast

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, Response

from app.api.errors import ApiError
from app.domain.ladder import PRESETS
from app.storage import paths
from app.storage.db import get_job as db_get_job
from app.storage.state import get_client

router = APIRouter(tags=["Artifacts"])
_SEG = re.compile(r"^seg_\d{5}\.ts$")

HLS_MIME  = "application/vnd.apple.mpegurl"
TS_MIME   = "video/mp2t"
MP4_MIME  = "video/mp4"
JPEG_MIME = "image/jpeg"
HTML_MIME = "text/html"
VTT_MIME  = "text/vtt"

NO_CACHE  = "no-cache"
IMMUTABLE = "max-age=31536000, immutable"
SHORT     = "max-age=3600"


async def _resolve_status(job_id: str) -> str | None:
    """Hot status, falling back to the cold tier so artifacts stay servable after a Redis flush/TTL."""
    status = cast(str | None, await get_client().hget(f"job:{job_id}", "status"))
    if status is not None:
        return status
    row = await run_in_threadpool(db_get_job, job_id)
    return row["status"] if row else None


async def _guard(job_id: str) -> Path:
    """Check job is `done`; return its output dir. Raises 404/410 otherwise."""
    status = await _resolve_status(job_id)
    if status is None:
        raise ApiError(404, "NOT_FOUND", "job not found", job_id)
    if status == "expired":
        raise ApiError(410, "EXPIRED", "job artifacts have expired", job_id)
    if status != "done":
        raise ApiError(404, "NOT_READY", "artifacts not yet available", job_id)
    return paths.output_dir(job_id)


async def _guard_thumb(job_id: str) -> Path:
    """Looser guard for poster/sprite: the thumbs task writes these mid-chord,
    before `done`. Serve them as soon as they exist; 404 until then, 410 if expired."""
    status = await _resolve_status(job_id)
    if status is None:
        raise ApiError(404, "NOT_FOUND", "job not found", job_id)
    if status == "expired":
        raise ApiError(410, "EXPIRED", "job artifacts have expired", job_id)
    return paths.output_dir(job_id)


def _safe(job_dir: Path, *parts: str) -> Path:
    """Resolve path and assert it's under job_dir — second layer of traversal defense."""
    p = Path(job_dir, *parts).resolve()
    if not p.is_relative_to(job_dir.resolve()):
        raise ApiError(403, "FORBIDDEN", "invalid path", "")
    return p


@router.get("/jobs/{job_id}/playlist")
async def master_playlist(job_id: str):
    job_dir = await _guard(job_id)
    content = (job_dir / "master.m3u8").read_text()
    return Response(content, media_type=HLS_MIME, headers={"Cache-Control": NO_CACHE})


@router.get("/jobs/{job_id}/playlist/subs")
async def subtitle_playlist(job_id: str):
    job_dir = await _guard(job_id)
    path = job_dir / "subs.m3u8"
    if not path.exists():
        raise ApiError(404, "NOT_READY", "subtitles not available", job_id)
    return Response(path.read_text(), media_type=HLS_MIME, headers={"Cache-Control": NO_CACHE})


@router.get("/jobs/{job_id}/manifest")
async def manifest(job_id: str):
    """The result manifest: the rendition ladder (resolution, measured bandwidth, codecs), poster/sprite
    refs, web_remuxed. The watch page reads it to render the spec ladder. 404 until packaging writes it."""
    job_dir = await _guard(job_id)
    path = job_dir / "manifest.json"
    if not path.exists():
        raise ApiError(404, "NOT_READY", "manifest not available", job_id)
    return Response(path.read_text(), media_type="application/json", headers={"Cache-Control": SHORT})


@router.get("/jobs/{job_id}/storyboard")
async def storyboard(job_id: str):
    """Sprite-sheet geometry (cols/rows/tile size/interval) so the player + cards can map a timestamp
    to a tile for hover-scrub previews. Read from the manifest; 404 until packaging has written it."""
    job_dir = await _guard(job_id)
    path = job_dir / "manifest.json"
    if not path.exists():
        raise ApiError(404, "NOT_READY", "storyboard not available", job_id)
    sb = json.loads(path.read_text()).get("storyboard")
    if not sb:
        raise ApiError(404, "NOT_READY", "storyboard not available", job_id)
    return Response(json.dumps(sb), media_type="application/json", headers={"Cache-Control": SHORT})


@router.get("/jobs/{job_id}/subtitles")
async def subtitles(job_id: str):
    job_dir = await _guard(job_id)
    path = job_dir / "subtitles.vtt"
    if not path.exists():
        raise ApiError(404, "NOT_READY", "subtitles not available", job_id)
    return FileResponse(str(path), media_type=VTT_MIME, headers={"Cache-Control": NO_CACHE})


@router.get("/jobs/{job_id}/playlist/{preset}")
async def rendition_playlist(job_id: str, preset: str):
    if preset not in PRESETS:
        raise ApiError(404, "NOT_FOUND", "unknown preset", job_id)
    job_dir = await _guard(job_id)
    src = _safe(job_dir, preset, "index.m3u8")
    content = src.read_text()
    # rewrite seg_XXXXX.ts -> ../segments/{preset}/seg_XXXXX.ts
    # from /jobs/{id}/playlist/{preset}, one ../ reaches /jobs/{id}/, then segments/{preset}/
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
    return FileResponse(str(path), media_type=TS_MIME,
                        headers={"Cache-Control": IMMUTABLE})


@router.get("/jobs/{job_id}/file")
async def web_mp4(job_id: str):
    job_dir = await _guard(job_id)
    return FileResponse(str(job_dir / "web.mp4"), media_type=MP4_MIME,
                        headers={"Cache-Control": SHORT})


@router.get("/jobs/{job_id}/poster")
async def poster(job_id: str):
    job_dir = await _guard_thumb(job_id)
    path = job_dir / "poster.jpg"
    if not path.exists():
        raise ApiError(404, "NOT_READY", "poster not yet generated", job_id)
    return FileResponse(str(path), media_type=JPEG_MIME, headers={"Cache-Control": SHORT})


@router.get("/jobs/{job_id}/sprite")
async def sprite(job_id: str):
    job_dir = await _guard_thumb(job_id)
    path = job_dir / "sprite.jpg"
    if not path.exists():
        raise ApiError(404, "NOT_READY", "sprite not yet generated", job_id)
    return FileResponse(str(path), media_type=JPEG_MIME, headers={"Cache-Control": SHORT})


@router.get("/jobs/{job_id}/player")
async def player(job_id: str):
    job_dir = await _guard(job_id)
    return HTMLResponse((job_dir / "embed.html").read_text(),
                        headers={"Cache-Control": SHORT})