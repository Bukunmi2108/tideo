import logging
import subprocess
from celery.exceptions import SoftTimeLimitExceeded
from app.core.config import config
from app.storage import paths
from app.workers.base import TranscodeTask
from app.workers.celery_app import app

logger = logging.getLogger(__name__)

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
        logger.info("transcode ok job=%s -> %s", job_id, final)
        return {"status": "ok", "output": str(final)}
    except SoftTimeLimitExceeded:
        logger.error("transcode timeout job=%s code=%s", job_id, ENCODE_TIMEOUT)
        return {"status": "failed", "error": {"code": ENCODE_TIMEOUT, "message": "soft time limit exceeded"}}
    except RuntimeError as e:
        logger.error("transcode failed job=%s code=%s msg=%s", job_id, ENCODE_FAILED, e)
        return {"status": "failed", "error": {"code": ENCODE_FAILED, "message": str(e)}}
