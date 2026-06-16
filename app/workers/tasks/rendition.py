import json, subprocess, threading, time
from collections import deque
from celery.exceptions import SoftTimeLimitExceeded
from app.events.producer import emit
from app.events.topics import RENDITION_STARTED, RENDITION_COMPLETED, RENDITION_FAILED
from app.storage import paths
from app.storage.state import get_sync_client
from app.domain.ladder import PRESETS
from app.workers.ffmpeg import build_rendition_argv
from app.workers.ffprobe import SourceMeta
from app.workers.progress import Throttle, parse_progress_blocks, percent
from app.workers.base import TranscodeTask
from app.workers.celery_app import app
import logging

logger = logging.getLogger(__name__)

ENCODE_FAILED, ENCODE_TIMEOUT = "ENCODE_FAILED", "ENCODE_TIMEOUT"

def _write_progress(job_id, preset, pct):
    """Fail-OPEN: a Redis hiccup must not kill the encode."""
    try:
        r = get_sync_client()
        r.hset(f"job:{job_id}", f"progress:{preset}", f"{pct:.1f}")
        r.publish(f"progress:{job_id}", json.dumps({"preset": preset, "percent": pct}))
    except Exception:
        logger.warning("progress write failed job=%s preset=%s (continuing)", job_id, preset)

def _encode(argv, *, duration, on_pct):
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1)
    assert proc.stdout is not None and proc.stderr is not None   # guaranteed by PIPE
    out, err = proc.stdout, proc.stderr
    tail = deque(maxlen=50)
    drain = threading.Thread(target=lambda: [tail.append(l.rstrip()) for l in err], daemon=True)
    drain.start()
    try:
        for block in parse_progress_blocks(out):     # ends at stdout EOF
            on_pct(percent(block, duration))
        proc.wait()
    except SoftTimeLimitExceeded:
        proc.kill(); proc.wait(); raise
    drain.join(timeout=1)
    return proc.returncode, "\n".join(tail)

@app.task(base=TranscodeTask)
def rendition(job_id: str, preset_name: str, src: str, meta: dict) -> dict:
    m = SourceMeta(**meta)
    preset = PRESETS[preset_name]
    emit(RENDITION_STARTED, job_id, {"preset": preset_name})
    throttle = Throttle()
    final = paths.output_dir(job_id) / preset_name
    started = time.monotonic()
    try:
        with paths.atomic_dir(final) as tmp:
            argv = build_rendition_argv(m, preset, src, str(tmp), progress=True)
            rc, stderr = _encode(
                argv, duration=m.duration,
                on_pct=lambda p: throttle.should_emit(p) and _write_progress(job_id, preset_name, p),
            )
            if rc != 0:
                raise RuntimeError(stderr)
        _write_progress(job_id, preset_name, 100.0)          # confirmed success -> 100
        out_bytes = sum(f.stat().st_size for f in final.glob("*.ts"))
        secs = round(time.monotonic() - started, 1)
        emit(RENDITION_COMPLETED, job_id,
             {"preset": preset_name, "output_bytes": out_bytes, "encode_seconds": secs})
        return {"status": "ok", "preset": preset_name, "output_bytes": out_bytes}
    except SoftTimeLimitExceeded:
        emit(RENDITION_FAILED, job_id, {"preset": preset_name, "error_code": ENCODE_TIMEOUT})
        return {"status": "failed", "error": {"code": ENCODE_TIMEOUT}}
    except RuntimeError as e:
        emit(RENDITION_FAILED, job_id, {"preset": preset_name, "error_code": ENCODE_FAILED})
        logger.error("rendition failed job=%s preset=%s: %s", job_id, preset_name, e)
        return {"status": "failed", "error": {"code": ENCODE_FAILED, "message": str(e)}}