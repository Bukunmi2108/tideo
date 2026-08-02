import json
import os
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import NoReturn, cast

from celery.exceptions import SoftTimeLimitExceeded
from redis.exceptions import RedisError

from app.core.logging import bind_job, get_logger
from app.domain.errors import (
    ENCODE_TIMEOUT,
    TRANSCODE,
    TideoError,
    classify,
    make_error,
)
from app.domain.ladder import PRESETS
from app.domain.state import IllegalTransition
from app.events.producer import emit
from app.events.topics import (
    JOB_STARTED,
    RENDITION_COMPLETED,
    RENDITION_FAILED,
    RENDITION_STARTED,
)
from app.storage import paths
from app.storage.job_control import transition_status
from app.storage.state import get_sync_client
from app.workers.base import TranscodeTask
from app.workers.cancellation import JobCancelled, is_cancelled
from app.workers.celery_app import app
from app.workers.ffmpeg import build_rendition_argv
from app.workers.ffprobe import SourceMeta
from app.workers.progress import Throttle, parse_progress_blocks, percent
from app.workers.retry import backoff_seconds, max_retries_for

log = get_logger()


def _terminate_group(proc: subprocess.Popen) -> None:
    """Terminate FFmpeg's process group."""
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
    """Store a rendition error."""
    try:
        get_sync_client().hset(f"job:{job_id}", mapping={
            "error_code": err.code, "error_message": err.message, "error_stage": err.stage,
            "error_stderr": stderr[-4000:],
        })
    except (RedisError, OSError):
        log.warning("error_store_failed")


def _handle_failure(task, job_id: str, preset_name: str, err: TideoError, stderr: str = "") -> NoReturn:
    """Retry or raise a rendition failure."""
    attempt = task.request.retries
    limit = max_retries_for(err)
    if attempt < limit:
        delay = backoff_seconds(attempt)
        log.warning("retry_scheduled", preset=preset_name, code=err.code,
                    attempt=attempt + 1, limit=limit, retry_in=delay)
        raise task.retry(countdown=delay, exc=RuntimeError(err.code))
    emit(RENDITION_FAILED, job_id, {"preset": preset_name, "error_code": err.code})
    _store_error(job_id, err, stderr)
    raise RuntimeError(err.code)


def _mark_started(job_id) -> bool:
    """Mark the first rendition as started."""
    r = get_sync_client()
    try:
        nxt = transition_status(
            r,
            job_id,
            "transcoding",
            caller="rendition",
            extra={"started_at": datetime.now(UTC).isoformat()},
        )
    except IllegalTransition:
        return cast(str, r.hget(f"job:{job_id}", "status")) == "transcoding"
    if nxt:
        created = cast(str | None, r.hget(f"job:{job_id}", "created_at"))
        qw = round((datetime.now(UTC) - datetime.fromisoformat(created)).total_seconds(), 1) if created else None
        log.info("job_started", queue_wait_seconds=qw)   # enqueue->first-encode latency (8.3 metric)
        emit(JOB_STARTED, job_id, {})
        return True
    return False

def _write_progress(job_id, preset, pct):
    """Write rendition progress."""
    try:
        r = get_sync_client()
        r.hset(f"job:{job_id}", f"progress:{preset}", f"{pct:.1f}")
        r.publish(f"progress:{job_id}", json.dumps({"preset": preset, "percent": pct}))
    except (RedisError, OSError):
        log.warning("progress_write_failed", preset=preset)


def _remove_rendition(path, *, job_id: str, preset: str) -> None:
    try:
        shutil.rmtree(path)
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("cancel_output_cleanup_failed", job_id=job_id, preset=preset)

def _encode(argv, *, duration, on_pct, cancelled):
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
                    raise JobCancelled()
        proc.wait()
        if cancelled():
            raise JobCancelled()
    except (SoftTimeLimitExceeded, JobCancelled):
        _terminate_group(proc)
        raise
    drain.join(timeout=1)
    return proc.returncode, "\n".join(tail)

@app.task(bind=True, base=TranscodeTask)
def rendition(self, job_id: str, preset_name: str, src: str, meta: dict) -> dict:
    bind_job(job_id)
    if is_cancelled(job_id):
        return {"status": "cancelled", "job_id": job_id}
    m = SourceMeta(**meta)
    preset = PRESETS[preset_name]
    if not _mark_started(job_id):
        return {"status": "cancelled", "job_id": job_id}
    log.info("rendition_started", preset=preset_name)
    emit(RENDITION_STARTED, job_id, {"preset": preset_name})
    throttle = Throttle()
    final = paths.ensure_output_dir(job_id) / preset_name
    started = time.monotonic()
    try:
        with paths.atomic_dir(final) as tmp:
            argv = build_rendition_argv(m, preset, src, str(tmp), progress=True)
            if is_cancelled(job_id):
                raise JobCancelled()
            rc, stderr = _encode(
                argv, duration=m.duration,
                on_pct=lambda p: throttle.should_emit(p) and _write_progress(job_id, preset_name, p),
                cancelled=lambda: is_cancelled(job_id),
            )
            if rc != 0:
                err = classify(rc, stderr, stage=TRANSCODE)
                log.error("rendition_failed", preset=preset_name, code=err.code, returncode=rc, stderr=stderr)
                _handle_failure(self, job_id, preset_name, err, stderr)  # retries or raises (tmp cleaned by atomic_dir)
            if is_cancelled(job_id):
                raise JobCancelled()
        if is_cancelled(job_id):
            raise JobCancelled()
        _write_progress(job_id, preset_name, 100.0)          # confirmed success -> 100
        out_bytes = sum(f.stat().st_size for f in final.glob("*.ts"))
        secs = round(time.monotonic() - started, 1)
        ratio = round(secs / m.duration, 3) if m.duration else None   # encode-time / media-seconds (8.3)
        log.info("rendition_completed", preset=preset_name, output_bytes=out_bytes,
                 encode_seconds=secs, encode_ratio=ratio)
        emit(RENDITION_COMPLETED, job_id,
             {"preset": preset_name, "output_bytes": out_bytes, "encode_seconds": secs})
        return {"status": "ok", "preset": preset_name, "output_bytes": out_bytes, "encode_seconds": secs}
    except JobCancelled:
        _remove_rendition(final, job_id=job_id, preset=preset_name)
        log.info("rendition_cancelled", preset=preset_name)
        return {"status": "cancelled", "job_id": job_id}
    except SoftTimeLimitExceeded:
        err = make_error(ENCODE_TIMEOUT, "encode exceeded the time limit", TRANSCODE)
        _handle_failure(self, job_id, preset_name, err)
