import json
import math
import shutil
from html import escape
from pathlib import Path
from typing import cast

from redis.exceptions import RedisError

from app.core.config import config
from app.core.logging import bind_job, get_logger
from app.domain.ladder import PRESETS
from app.domain.playlist import Variant, avc1_codec, bandwidths, build_manifest
from app.domain.state import TERMINAL
from app.events.producer import emit
from app.events.topics import JOB_COMPLETED
from app.storage import paths, terminal_outbox
from app.storage.job_control import transition_status
from app.storage.state import get_sync_client
from app.workers.base import PackageTask
from app.workers.cancellation import CancellationUnavailable, is_cancelled
from app.workers.celery_app import app
from app.workers.process import run_process
from app.workers.retry import MAX_RETRIES, backoff_seconds
from app.workers.source import release_source
from app.workers.subtitles import refresh_master
from app.workers.tasks.thumbs import write_poster, write_sprite

log = get_logger()


def _probe_variant(seg_path: str) -> tuple[dict, float]:
    out = run_process(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,profile,level:format=start_time",
         "-of", "json", seg_path],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    data = json.loads(out.stdout)
    start_time = float(data["format"]["start_time"])
    if not math.isfinite(start_time):
        raise ValueError("rendition has an invalid media start time")
    return data["streams"][0], start_time


def _segment_measurements(playlist: Path) -> list[tuple[int, float]]:
    """Read the simple FFmpeg VOD playlist and measure each referenced segment."""
    base = playlist.parent.resolve()
    measurements: list[tuple[int, float]] = []
    duration: float | None = None
    for raw in playlist.read_text().splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF:"):
            if duration is not None:
                raise ValueError("rendition playlist has consecutive EXTINF tags")
            try:
                duration = float(line.removeprefix("#EXTINF:").split(",", 1)[0])
            except ValueError as exc:
                raise ValueError("rendition playlist has an invalid EXTINF duration") from exc
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("rendition playlist has a non-positive EXTINF duration")
        elif line and not line.startswith("#"):
            if duration is None:
                raise ValueError("rendition playlist has a segment without EXTINF")
            segment = (playlist.parent / line).resolve()
            if not segment.is_relative_to(base):
                raise ValueError("rendition playlist segment escapes its directory")
            measurements.append((segment.stat().st_size, duration))
            duration = None
    if duration is not None or not measurements:
        raise ValueError("rendition playlist has no complete media segments")
    return measurements


def _variant(
    job_dir: str,
    preset: str,
    *,
    has_audio: bool,
) -> tuple[Variant, float]:
    s, start_time = _probe_variant(f"{job_dir}/{preset}/seg_00000.ts")
    peak, average = bandwidths(
        _segment_measurements(Path(job_dir) / preset / "index.m3u8")
    )
    codecs = avc1_codec(s["profile"], int(s["level"]))
    if has_audio:
        codecs += ",mp4a.40.2"
    return (
        Variant(preset, peak, int(s["width"]), int(s["height"]), codecs, average),
        start_time,
    )


def _highest(presets: list[str]) -> str:
    order = list(PRESETS)                                      # catalog is ordered highest-first
    return min(presets, key=order.index)


def _lowest(presets: list[str]) -> str:
    order = list(PRESETS)
    return max(presets, key=order.index)


def _web_mp4(
    src: str,
    out: Path,
    *,
    web_safe: bool,
    top_playlist: str,
    cancelled,
) -> bool:
    """Build web.mp4 without encoding the source a second time."""
    media_input = src if web_safe else top_playlist
    with paths.atomic_path(out) as tmp:
        run_process(
            [
                "ffmpeg",
                "-nostdin",
                "-y",
                "-i",
                media_input,
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(tmp),
            ],
            check=True,
            timeout=230,
            cancelled=cancelled,
        )
    return web_safe


def _package(results, job_id: str) -> dict:
    bind_job(job_id)
    results = results if isinstance(results, list) else [results]
    r = get_sync_client()
    current = cast(str, r.hget(f"job:{job_id}", "status")) or ""
    if current in TERMINAL:
        if current == "cancelled":
            _cancel_package(r, job_id, config.output_dir / job_id)
        return {"status": current, "job_id": job_id}
    rec = r.hgetall(f"job:{job_id}")
    meta = json.loads(rec["source_meta"])
    duration = meta["duration"]
    job_dir = paths.ensure_output_dir(job_id)

    if is_cancelled(job_id):
        return _cancel_package(r, job_id, job_dir)

    renditions = [res for res in results if "preset" in res]
    measured_variants = [
        _variant(
            str(job_dir),
            res["preset"],
            has_audio=bool(meta.get("has_audio")),
        )
        for res in renditions
    ]
    variants = [variant for variant, _ in measured_variants]
    media_start_time = measured_variants[0][1]

    top = _highest([v.preset for v in variants])
    if is_cancelled(job_id):
        return _cancel_package(r, job_id, job_dir)
    remuxed = _web_mp4(
        cast(str, rec["source_path"]),
        job_dir / "web.mp4",
        web_safe=(rec.get("web_safe") == "true"),
        top_playlist=f"{job_dir}/{top}/index.m3u8",
        cancelled=lambda: is_cancelled(job_id),
    )
    log.info("web_mp4_built", mode="source_remux" if remuxed else "rendition_remux")

    low = _lowest([v.preset for v in variants])
    if is_cancelled(job_id):
        return _cancel_package(r, job_id, job_dir)
    write_poster(
        job_dir,
        f"{job_dir}/{top}/index.m3u8",
        duration,
        cancelled=lambda: is_cancelled(job_id),
    )
    storyboard = write_sprite(
        job_dir,
        f"{job_dir}/{low}/index.m3u8",
        duration,
        meta.get("fps") or 30.0,
        cancelled=lambda: is_cancelled(job_id),
    )

    if is_cancelled(job_id):
        return _cancel_package(r, job_id, job_dir)

    manifest = build_manifest(job_id, duration, variants, web_remuxed=remuxed,
                              created_at=cast(str, rec.get("created_at")),
                              media_start_time=media_start_time, storyboard=storyboard)
    with paths.atomic_path(job_dir / "manifest.json") as tmp:
        tmp.write_text(json.dumps(manifest, indent=2))

    # master last, via the shared writer: includes the subtitles track iff transcription already landed
    # the VTT (it runs alongside the ladder). If it lands later, the transcribe task rewrites master then.
    refresh_master(job_dir, variants, duration, media_start_time=media_start_time)

    name = escape(cast(str, rec.get("source_filename", "")))  # filename is user input -> escape it
    # "playlist" is relative to /jobs/{id}/player -> resolves to /jobs/{id}/playlist
    with paths.atomic_path(job_dir / "embed.html") as tmp:
        tmp.write_text(
            f'<!-- {name} --><video id="v" controls style="width:100%"></video>'
            '<script src="https://cdn.jsdelivr.net/npm/hls.js@1"></script>'
            '<script>var h=new Hls({debug:true});h.loadSource("playlist");h.attachMedia(document.getElementById("v"));</script>'
        )

    nxt = transition_status(
        r,
        job_id,
        "done",
        caller="package",
        extra={"rendition_results": json.dumps(results)},
    )
    if nxt:
        terminal_outbox.drain_one(r, job_id)
        release_source(r, job_id, "package")   # reclaim the upload once transcribe is also done with it
        emit(JOB_COMPLETED, job_id, {
            "renditions": len(variants),
            "output_bytes_total": sum(res.get("output_bytes", 0) for res in results),
        })
        # poke the progress channel so a live WS relay wakes and detects the terminal status
        r.publish(f"progress:{job_id}", json.dumps({"event": "terminal"}))
    else:
        return _cancel_package(r, job_id, job_dir)
    return {"status": nxt, "job_id": job_id, "master": "master.m3u8"}


@app.task(bind=True, base=PackageTask)
def package(self, results, job_id: str) -> dict:
    try:
        return _package(results, job_id)
    except CancellationUnavailable as exc:
        raise self.retry(
            exc=exc,
            countdown=backoff_seconds(self.request.retries),
            max_retries=MAX_RETRIES,
        )


def _cancel_package(r, job_id: str, job_dir) -> dict:
    try:
        shutil.rmtree(job_dir)
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("cancel_output_cleanup_failed")
    try:
        release_source(r, job_id, "package")
    except (RedisError, OSError):
        log.warning("source_release_failed")
    log.info("package_cancelled")
    return {"status": "cancelled", "job_id": job_id}
