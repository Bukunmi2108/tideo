from app.workers.ffprobe import SourceMeta, SOURCE_LIMITS_EXCEEDED, InspectError

LADDER = [("1080p", 1080), ("720p", 720), ("480p", 480), ("360p", 360)]

MAX_W, MAX_H = 7680, 4320
MAX_BITRATE = 200_000_000

def recommended_presets(display_height: int) -> list[str]:
    rungs = [name for name, h in LADDER if h <= display_height]
    return rungs or ["360p"]

def web_safe(meta: SourceMeta) -> tuple[bool, str | None]:
    reasons = []
    if meta.video_codec != "h264":
        reasons.append(f"video {meta.video_codec} (need h264)")
    if meta.has_audio and meta.audio_codec != "aac":
        reasons.append(f"audio {meta.audio_codec} (need aac)")
    if not _is_mp4_mov(meta.container):
        reasons.append(f"container {meta.container} (need mp4/mov)")
    return (not reasons, "; ".join(reasons) or None)

def _is_mp4_mov(container: str) -> bool:
    parts = container.split(",")
    return "mp4" in parts or "mov" in parts

def check_caps(meta: SourceMeta, max_seconds: int) -> None:
    if meta.width > MAX_W or meta.height > MAX_H:
        raise InspectError(SOURCE_LIMITS_EXCEEDED, f"{meta.width}x{meta.height} exceeds {MAX_W}x{MAX_H}")
    if meta.duration > max_seconds:
        raise InspectError(SOURCE_LIMITS_EXCEEDED, f"duration {meta.duration:.0f}s exceeds {max_seconds}s")
    if meta.bitrate and meta.bitrate > MAX_BITRATE:
        raise InspectError(SOURCE_LIMITS_EXCEEDED, f"bitrate {meta.bitrate} exceeds {MAX_BITRATE}")