import json
from typing import cast

from celery.exceptions import Retry
from redis.exceptions import RedisError

from app.core.config import config
from app.core.logging import bind_job, get_logger
from app.core.ratelimit import RetryIn, acquire, parse_rate
from app.domain.errors import STT_BAD_AUDIO, STT_INTERNAL, STT_RATE_LIMITED
from app.domain.vtt import render_vtt
from app.storage import paths
from app.storage.db import update_subtitles
from app.storage.state import get_sync_client
from app.workers.audio import extract_audio, has_audio
from app.workers.base import TranscribeTask
from app.workers.cancellation import is_cancelled
from app.workers.celery_app import app
from app.workers.retry import backoff_seconds
from app.workers.source import release_source
from app.workers.stt import get_provider
from app.workers.stt.base import SttCancelled, SttUpstreamError
from app.workers.subtitles import attach_subtitles

log = get_logger()

PACING_CAP = 1000   # backstop only; our own counters decide when to stop. Celery must not pre-empt.


def _set_status(job_id: str, payload: dict) -> None:
    """Store subtitle status and release its source claim."""
    r = get_sync_client()
    try:
        r.hset(f"job:{job_id}", "subtitles", json.dumps(payload))
    except (RedisError, OSError):
        log.warning("subtitles_hot_write_failed")
    update_subtitles(job_id, payload)
    try:
        release_source(r, job_id, "transcribe")
    except (RedisError, OSError):
        log.warning("source_release_failed")     # cleanup, not correctness — never let it mask the status write


def _handle_failure(task, job_id: str, exc: SttUpstreamError) -> dict:
    """Retry or fail soft after an STT error."""
    err = exc.error
    if is_cancelled(job_id):
        return _finish_cancelled(job_id)
    if err.code == STT_RATE_LIMITED:
        delay = exc.retry_after if exc.retry_after is not None else backoff_seconds(task.request.retries)
        log.warning("stt_upstream_throttled", retry_in=delay)
        raise task.retry(countdown=delay, max_retries=PACING_CAP)
    if err.retryable:
        attempts = get_sync_client().hincrby(f"job:{job_id}", "stt_attempts", 1)
        if attempts <= config.stt_max_retries:
            delay = backoff_seconds(attempts - 1)
            log.warning("stt_retry_scheduled", code=err.code, attempt=attempts, retry_in=delay)
            raise task.retry(countdown=delay, max_retries=PACING_CAP)
    log.warning("subtitles_failed", code=err.code, reason=err.message)
    _set_status(job_id, {"status": "failed", "code": err.code, "reason": err.message})
    return {"status": "failed"}


def _finish_cancelled(job_id: str, job_dir=None) -> dict:
    job_dir = job_dir or (config.output_dir / job_id)
    for name in ("audio.wav", "subtitles.vtt", "subs.m3u8"):
        (job_dir / name).unlink(missing_ok=True)
    _set_status(job_id, {"status": "cancelled"})
    log.info("transcription_cancelled")
    return {"status": "cancelled"}


def _run(task, job_id: str, src: str, meta: dict) -> dict:
    if is_cancelled(job_id):
        return _finish_cancelled(job_id)
    limit, window = parse_rate(config.stt_rate_limit)
    decision = acquire("stt:global", limit, window)
    if isinstance(decision, RetryIn):
        if is_cancelled(job_id):
            return _finish_cancelled(job_id)
        log.info("stt_rate_limited", retry_in=decision.seconds)
        raise task.retry(countdown=decision.seconds, max_retries=PACING_CAP)

    if not has_audio(meta):
        log.info("stt_no_audio")
        _set_status(job_id, {"status": "none", "reason": "no audio stream"})
        return {"status": "none"}

    job_dir = paths.output_dir(job_id)
    wav = job_dir / "audio.wav"
    try:
        extract_audio(src, str(wav))
    except Exception as e:  # noqa: BLE001
        log.warning("audio_extract_failed", error=str(e))
        _set_status(job_id, {"status": "failed", "code": STT_BAD_AUDIO, "reason": "audio extraction failed"})
        return {"status": "failed"}

    if is_cancelled(job_id):
        wav.unlink(missing_ok=True)
        return _finish_cancelled(job_id, job_dir)

    try:
        segments = get_provider().transcribe(str(wav), cancelled=lambda: is_cancelled(job_id))
    except SttCancelled:
        return _finish_cancelled(job_id, job_dir)
    except SttUpstreamError as e:
        return _handle_failure(task, job_id, e)
    finally:
        wav.unlink(missing_ok=True)

    if is_cancelled(job_id):
        return _finish_cancelled(job_id, job_dir)

    with paths.atomic_path(job_dir / "subtitles.vtt") as tmp:
        tmp.write_text(render_vtt(segments))
    if is_cancelled(job_id):
        return _finish_cancelled(job_id, job_dir)
    attach_subtitles(job_id, cast(float, meta.get("duration") or 0.0))
    if is_cancelled(job_id):
        return _finish_cancelled(job_id, job_dir)
    _set_status(job_id, {"status": "ready", "url": f"/jobs/{job_id}/subtitles"})
    log.info("subtitles_ready", cues=len(segments))
    return {"status": "ready"}


@app.task(bind=True, base=TranscribeTask)
def transcribe(self, job_id: str, src: str, meta: dict) -> dict:
    """Transcribe a job without failing its rendition ladder."""
    bind_job(job_id)
    try:
        return _run(self, job_id, src, meta)
    except Retry:
        raise
    except Exception:
        log.exception("subtitles_internal_error")
        _set_status(job_id, {"status": "failed", "code": STT_INTERNAL, "reason": "internal error"})
        return {"status": "failed"}
