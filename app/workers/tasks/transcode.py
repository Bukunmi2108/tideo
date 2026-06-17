import subprocess
from celery.exceptions import SoftTimeLimitExceeded
from app.core.config import config
from app.core.logging import get_logger
from app.storage import paths
from app.workers.base import TranscodeTask
from app.workers.celery_app import app

log = get_logger()

ENCODE_FAILED = "ENCODE_FAILED"
ENCODE_TIMEOUT = "ENCODE_TIMEOUT"


def _ffmpeg_480p(src: str, out: str) -> list[str]:
    # Hardcoded; the preset catalog replaces this.
    return [
        "ffmpeg", "-y", "-loglevel", "error", "-i", src,
        "-vf", "scale=-2:480",
        "-c:v", "libx264", "-preset", config.x264_preset, "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-movflags", "+faststart",
        out,
    ]


def _tail(text: str, n: int = 50) -> str:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def _run_ffmpeg(argv: list[str]) -> tuple[int, str]:
    proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    try:
        _, stderr = proc.communicate()
        return proc.returncode, stderr or ""
    except SoftTimeLimitExceeded:
        proc.kill()
        proc.wait()
        raise

@app.task(base=TranscodeTask)
def transcode(job_id: str, src: str) -> dict:
    final = paths.output_dir(job_id) / "480p.mp4"
    try:
        with paths.atomic_path(final) as tmp:
            rc, stderr = _run_ffmpeg(_ffmpeg_480p(src, str(tmp)))
            if rc != 0:
                raise RuntimeError(_tail(stderr))
        log.info("transcode_completed", job_id=job_id, output=str(final))
        return {"status": "ok", "output": str(final)}
    except SoftTimeLimitExceeded:
        log.error("transcode_timeout", job_id=job_id, code=ENCODE_TIMEOUT)
        return {"status": "failed", "error": {"code": ENCODE_TIMEOUT, "message": "soft time limit exceeded"}}
    except RuntimeError as e:
        log.error("transcode_failed", job_id=job_id, code=ENCODE_FAILED, error=str(e))
        return {"status": "failed", "error": {"code": ENCODE_FAILED, "message": str(e)}}
