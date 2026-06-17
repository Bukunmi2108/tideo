import json
from pathlib import Path

from app.domain.playlist import Variant, build_master, build_subtitle_media_playlist
from app.storage import paths


def refresh_master(job_dir: Path, variants: list[Variant], duration: float) -> None:
    """Write master.m3u8 (atomic), including the subtitles track iff subtitles.vtt is present, and write
    the subtitle media playlist when it is. Idempotent and order-free: package always calls it after the
    ladder, transcribe always calls it after the VTT lands, so whichever finishes last produces a master
    that matches what's on disk — the late-arrival rewrite needs no locking."""
    has_subs = (job_dir / "subtitles.vtt").exists()
    with paths.atomic_path(job_dir / "master.m3u8") as tmp:
        tmp.write_text(build_master(variants, has_subtitles=has_subs))
    if has_subs:
        with paths.atomic_path(job_dir / "subs.m3u8") as tmp:
            tmp.write_text(build_subtitle_media_playlist(duration))


def _variants_from_manifest(manifest: dict) -> list[Variant]:
    out = []
    for r in manifest.get("renditions", []):
        w, h = (int(x) for x in r["resolution"].split("x"))
        out.append(Variant(r["preset"], r["bandwidth"], w, h, r["codecs"]))
    return out


def attach_subtitles(job_id: str, duration: float) -> bool:
    """Called by transcribe once the VTT is written. Rewrites master from the manifest if the ladder has
    already packaged; if not, returns False — package will pick the VTT up when it runs refresh_master."""
    job_dir = paths.output_dir(job_id)
    manifest_path = job_dir / "manifest.json"
    if not manifest_path.exists():
        return False
    refresh_master(job_dir, _variants_from_manifest(json.loads(manifest_path.read_text())), duration)
    return True
