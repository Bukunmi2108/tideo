import json
from pathlib import Path

from app.domain.playlist import Variant, build_master, build_subtitle_media_playlist
from app.domain.vtt import with_timestamp_map
from app.storage import paths
from app.workers.process import run_process


def _probe_media_start(job_dir: Path, preset: str) -> float:
    out = run_process(
        ["ffprobe", "-v", "error", "-show_entries", "format=start_time",
         "-of", "json", str(job_dir / preset / "seg_00000.ts")],
        capture_output=True,
        text=True,
        check=True,
        timeout=20,
    )
    return float(json.loads(out.stdout)["format"]["start_time"])


def refresh_master(job_dir: Path, variants: list[Variant], duration: float,
                   *, media_start_time: float | None = None) -> None:
    master = job_dir / "master.m3u8"
    with paths.path_lock(master):
        has_subs = (job_dir / "subtitles.vtt").exists()
        if has_subs:
            start = (media_start_time if media_start_time is not None
                     else _probe_media_start(job_dir, variants[0].preset))
            vtt = job_dir / "subtitles.vtt"
            source = vtt.read_text()
            mapped = with_timestamp_map(source, start)
            if mapped != source:
                with paths.atomic_path(vtt) as tmp:
                    tmp.write_text(mapped)
            with paths.atomic_path(job_dir / "subs.m3u8") as tmp:
                tmp.write_text(build_subtitle_media_playlist(duration))
        # Publish the master only after every referenced caption artifact is ready.
        with paths.atomic_path(master) as tmp:
            tmp.write_text(build_master(variants, has_subtitles=has_subs))


def _variants_from_manifest(manifest: dict) -> list[Variant]:
    out = []
    for r in manifest.get("renditions", []):
        w, h = (int(x) for x in r["resolution"].split("x"))
        out.append(Variant(r["preset"], r["bandwidth"], w, h, r["codecs"],
                           r.get("average_bandwidth")))
    return out


def attach_subtitles(job_id: str, duration: float) -> bool:
    """Called by transcribe once the VTT is written. Rewrites master from the manifest if the ladder has
    already packaged; if not, returns False — package will pick the VTT up when it runs refresh_master."""
    job_dir = paths.output_dir(job_id)
    manifest_path = job_dir / "manifest.json"
    if not manifest_path.exists():
        return False
    manifest = json.loads(manifest_path.read_text())
    refresh_master(
        job_dir,
        _variants_from_manifest(manifest),
        duration,
        media_start_time=manifest.get("media_start_time"),
    )
    return True
