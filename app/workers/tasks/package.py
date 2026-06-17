import json
import subprocess
from html import escape
from typing import cast
from app.core.config import config
from app.core.logging import bind_job, get_logger
from app.domain.ladder import PRESETS
from app.domain.playlist import Variant, avc1_codec, bandwidth, build_manifest
from app.domain.state import transition
from app.events.producer import emit
from app.events.topics import JOB_COMPLETED
from app.storage import paths
from app.storage.db import persist_terminal
from app.storage.state import get_sync_client, write_status
from app.workers.base import PackageTask
from app.workers.celery_app import app
from app.workers.source import release_source
from app.workers.subtitles import refresh_master
from app.workers.tasks.thumbs import write_poster, write_sprite

log = get_logger()


@app.task(base=PackageTask)
def noop() -> str:
    return "package ok"


def _probe_variant(seg_path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,profile,level", "-of", "json", seg_path],
        capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout)["streams"][0]


def _variant(job_dir: str, preset: str, output_bytes: int, duration: float) -> Variant:
    s = _probe_variant(f"{job_dir}/{preset}/seg_00000.ts")     # actual encoded dims + profile/level
    codecs = f"{avc1_codec(s['profile'], int(s['level']))},mp4a.40.2"
    return Variant(preset, bandwidth(output_bytes, duration), int(s["width"]), int(s["height"]), codecs)


def _highest(presets: list[str]) -> str:
    order = list(PRESETS)                                      # catalog is ordered highest-first
    return min(presets, key=order.index)


def _lowest(presets: list[str]) -> str:
    order = list(PRESETS)
    return max(presets, key=order.index)


def _web_mp4(src: str, out: str, *, web_safe: bool, top: str) -> bool:
    """web.mp4: remux (-c copy) when web-safe, else re-encode at the top rung. Returns whether remuxed."""
    if web_safe:
        argv = ["ffmpeg", "-nostdin", "-y", "-i", src, "-c", "copy", "-movflags", "+faststart", out]
    else:
        p = PRESETS[top]
        vf = f"scale=w={p.width}:h={p.height}:force_original_aspect_ratio=decrease:force_divisible_by=2"
        argv = ["ffmpeg", "-nostdin", "-y", "-i", src, "-vf", vf,
                "-c:v", "libx264", "-preset", config.x264_preset, "-profile:v", p.profile,
                "-b:v", p.v_bitrate, "-maxrate", p.maxrate, "-bufsize", p.bufsize,
                "-c:a", "aac", "-b:a", p.a_bitrate, "-movflags", "+faststart", out]
    subprocess.run(argv, check=True)
    return web_safe


@app.task(base=PackageTask)
def package(results, job_id: str) -> dict:
    """Chord callback: assemble master.m3u8 + web.mp4 + manifest, then mark the job done.
    Fires once all renditions succeed; the chord prepends their results as the first arg."""
    bind_job(job_id)
    results = results if isinstance(results, list) else [results]
    r = get_sync_client()
    if cast(str, r.hget(f"job:{job_id}", "status")) == "done":
        return {"status": "done", "job_id": job_id}
    rec = r.hgetall(f"job:{job_id}")
    meta = json.loads(rec["source_meta"])
    duration = meta["duration"]
    job_dir = paths.output_dir(job_id)

    renditions = [res for res in results if "preset" in res]   # thumbs result has no "preset"
    variants = [_variant(str(job_dir), res["preset"], res["output_bytes"], duration) for res in renditions]

    top = _highest([v.preset for v in variants])
    remuxed = _web_mp4(cast(str, rec["source_path"]), str(job_dir / "web.mp4"),
                       web_safe=(rec.get("web_safe") == "true"), top=top)
    log.info("web_mp4_built", mode="remux" if remuxed else "reencode")

    low = _lowest([v.preset for v in variants])
    write_poster(job_dir, f"{job_dir}/{top}/index.m3u8", duration)
    write_sprite(job_dir, f"{job_dir}/{low}/index.m3u8", duration, meta.get("fps") or 30.0)

    manifest = build_manifest(job_id, duration, variants,
                              web_remuxed=remuxed, created_at=cast(str, rec.get("created_at")))
    (job_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # master last, via the shared writer: includes the subtitles track iff transcription already landed
    # the VTT (it runs alongside the ladder). If it lands later, the transcribe task rewrites master then.
    refresh_master(job_dir, variants, duration)

    name = escape(cast(str, rec.get("source_filename", "")))  # filename is user input -> escape it
    # "playlist" is relative to /jobs/{id}/player -> resolves to /jobs/{id}/playlist
    (job_dir / "embed.html").write_text(
        f'<!-- {name} --><video id="v" controls style="width:100%"></video>'
        '<script src="https://cdn.jsdelivr.net/npm/hls.js@1"></script>'
        '<script>var h=new Hls({debug:true});h.loadSource("playlist");h.attachMedia(document.getElementById("v"));</script>'
    )

    # terminal: done + results refs + job.completed + durable Postgres row.
    cur = cast(str, r.hget(f"job:{job_id}", "status")) or ""
    nxt = transition(cur, "done", job_id=job_id, caller="package")
    if nxt:
        write_status(r, job_id, nxt, extra={
            "results": json.dumps({"master": "master.m3u8", "web_mp4": "web.mp4", "manifest": "manifest.json"}),
        })
        r.expire(f"job:{job_id}", config.output_ttl_days * 86400)   # hot state yields to Postgres after the window
        persist_terminal(job_id, r.hgetall(f"job:{job_id}"), results=results)
        release_source(r, job_id, "package")   # reclaim the upload once transcribe is also done with it
        emit(JOB_COMPLETED, job_id, {
            "renditions": len(variants),
            "output_bytes_total": sum(res.get("output_bytes", 0) for res in results),
        })
        # poke the progress channel so a live WS relay wakes and detects the terminal status
        r.publish(f"progress:{job_id}", json.dumps({"event": "terminal"}))
    return {"status": nxt, "job_id": job_id, "master": "master.m3u8"}
