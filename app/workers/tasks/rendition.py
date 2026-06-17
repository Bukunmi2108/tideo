import json, os, signal, subprocess, threading, time
from collections import deque
from datetime import datetime, timezone
from typing import NoReturn, cast
from celery.exceptions import Ignore, SoftTimeLimitExceeded
from app.domain.errors import TideoError, classify, make_error, ENCODE_TIMEOUT, TRANSCODE
from app.domain.state import transition
from app.events.producer import emit
from app.events.topics import JOB_STARTED, RENDITION_STARTED, RENDITION_COMPLETED, RENDITION_FAILED
from app.storage import paths
from app.storage.state import get_sync_client
from app.domain.ladder import PRESETS
from app.workers.ffmpeg import build_rendition_argv
from app.workers.ffprobe import SourceMeta
from app.workers.progress import Throttle, parse_progress_blocks, percent
from app.workers.base import TranscodeTask
from app.workers.retry import backoff_seconds, max_retries_for
from app.workers.celery_app import app
import logging

logger = logging.getLogger(__name__)


class Cancelled(Exception):
    pass


def _is_cancelled(job_id: str) -> bool:
    try:
        return bool(get_sync_client().exists(f"cancel:{job_id}"))
    except Exception:
        return False


def _terminate_group(proc: subprocess.Popen) -> None:
    """Kill FFmpeg's whole process group (it ran in its own session) — SIGTERM, then SIGKILL
    after a grace period. Revoking the Celery task alone leaves FFmpeg an orphan."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        return
    os.killpg(pgid, signal.SIGTERM)
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        os.killpg(pgid, signal.SIGKILL)
        proc.wait()


def _store_error(job_id: str, err: TideoError, stderr: str = "") -> None:
    """Record the classified failure on the hash (status stays transcoding; fail_job transitions). Fail-open."""
    try:
        get_sync_client().hset(f"job:{job_id}", mapping={
            "error_code": err.code, "error_message": err.message, "error_stage": err.stage,
            "error_stderr": stderr[-4000:],
        })
    except Exception:
        logger.warning("error store failed job=%s (continuing)", job_id)


def _handle_failure(task, job_id: str, preset_name: str, err: TideoError, stderr: str = "") -> NoReturn:
    """Retry a retryable failure with full-jitter backoff; on exhaustion or a permanent
    error, emit the failed fact and raise so the chord fails the job (ADR-3)."""
    attempt = task.request.retries  # 0 on the first run
    limit = max_retries_for(err)
    if attempt < limit:
        delay = backoff_seconds(attempt)
        logger.warning("rendition retry job=%s preset=%s code=%s attempt=%d/%d retry_in=%.1fs",
                       job_id, preset_name, err.code, attempt + 1, limit, delay)
        raise task.retry(countdown=delay, exc=RuntimeError(err.code))
    emit(RENDITION_FAILED, job_id, {"preset": preset_name, "error_code": err.code})
    _store_error(job_id, err, stderr)
    raise RuntimeError(err.code)


def _mark_started(job_id):
    """First rendition to begin flips queued->transcoding and emits job.started, exactly once.
    SET NX picks the single winner; parallel siblings skip (no illegal re-transition / dup event)."""
    r = get_sync_client()
    if not r.set(f"started:{job_id}", "1", nx=True):
        return
    cur = cast(str, r.hget(f"job:{job_id}", "status")) or ""
    nxt = transition(cur, "transcoding", job_id=job_id, caller="rendition")
    if nxt:
        r.hset(f"job:{job_id}", mapping={
            "status": nxt,
            "started_at": datetime.now(timezone.utc).isoformat(),
        })
        emit(JOB_STARTED, job_id, {})

def _write_progress(job_id, preset, pct):
    """Fail-OPEN: a Redis hiccup must not kill the encode."""
    try:
        r = get_sync_client()
        r.hset(f"job:{job_id}", f"progress:{preset}", f"{pct:.1f}")
        r.publish(f"progress:{job_id}", json.dumps({"preset": preset, "percent": pct}))
    except Exception:
        logger.warning("progress write failed job=%s preset=%s (continuing)", job_id, preset)

def _encode(argv, *, duration, on_pct, cancelled):
    # own session -> own process group, so _terminate_group can take FFmpeg (and any children) down
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, bufsize=1, start_new_session=True)
    assert proc.stdout is not None and proc.stderr is not None   # guaranteed by PIPE
    out, err = proc.stdout, proc.stderr
    tail = deque(maxlen=50)
    drain = threading.Thread(target=lambda: [tail.append(l.rstrip()) for l in err], daemon=True)
    drain.start()
    last_check = time.monotonic()
    try:
        for block in parse_progress_blocks(out):     # ends at stdout EOF
            on_pct(percent(block, duration))
            now = time.monotonic()
            if now - last_check >= 1.0:               # poll the cancel flag ~1/s
                last_check = now
                if cancelled():
                    _terminate_group(proc)
                    raise Cancelled()
        proc.wait()
    except (SoftTimeLimitExceeded, Cancelled):
        _terminate_group(proc)
        raise
    drain.join(timeout=1)
    return proc.returncode, "\n".join(tail)

@app.task(bind=True, base=TranscodeTask)
def rendition(self, job_id: str, preset_name: str, src: str, meta: dict) -> dict:
    m = SourceMeta(**meta)
    preset = PRESETS[preset_name]
    emit(RENDITION_STARTED, job_id, {"preset": preset_name})
    _mark_started(job_id)                                # first rendition flips job -> transcoding
    throttle = Throttle()
    final = paths.output_dir(job_id) / preset_name
    started = time.monotonic()
    try:
        with paths.atomic_dir(final) as tmp:
            argv = build_rendition_argv(m, preset, src, str(tmp), progress=True)
            rc, stderr = _encode(
                argv, duration=m.duration,
                on_pct=lambda p: throttle.should_emit(p) and _write_progress(job_id, preset_name, p),
                cancelled=lambda: _is_cancelled(job_id),
            )
            if rc != 0:
                err = classify(rc, stderr, stage=TRANSCODE)
                logger.error("rendition failed job=%s preset=%s code=%s rc=%s: %s",
                             job_id, preset_name, err.code, rc, stderr)
                _handle_failure(self, job_id, preset_name, err, stderr)  # retries or raises (tmp cleaned by atomic_dir)
        _write_progress(job_id, preset_name, 100.0)          # confirmed success -> 100
        out_bytes = sum(f.stat().st_size for f in final.glob("*.ts"))
        secs = round(time.monotonic() - started, 1)
        emit(RENDITION_COMPLETED, job_id,
             {"preset": preset_name, "output_bytes": out_bytes, "encode_seconds": secs})
        return {"status": "ok", "preset": preset_name, "output_bytes": out_bytes, "encode_seconds": secs}
    except Cancelled:
        logger.info("rendition cancelled job=%s preset=%s", job_id, preset_name)
        raise Ignore()  # job already marked cancelled by the API; don't fail it via link_error
    except SoftTimeLimitExceeded:
        err = make_error(ENCODE_TIMEOUT, "encode exceeded the time limit", TRANSCODE)
        _handle_failure(self, job_id, preset_name, err)