from typing import cast

from celery.exceptions import Retry, SoftTimeLimitExceeded
from redis.exceptions import RedisError

from app.core.config import config
from app.core.logging import bind_job, get_logger
from app.core.ratelimit import RetryIn, acquire
from app.domain.errors import STT_BAD_AUDIO, STT_INTERNAL, STT_UNAVAILABLE
from app.domain.state import TERMINAL
from app.domain.vtt import render_vtt
from app.storage import paths, terminal_outbox
from app.storage.state import get_sync_client
from app.workers.audio import extract_audio, has_audio
from app.workers.base import TranscribeTask
from app.workers.cancellation import CancellationUnavailable, JobCancelled, is_cancelled
from app.workers.celery_app import app
from app.workers.retry import MAX_RETRIES, backoff_seconds
from app.workers.source import release_source
from app.workers.stt.local import SttCancelled, SttError
from app.workers.stt.local import transcribe as transcribe_audio
from app.workers.subtitles import attach_subtitles

log = get_logger()

PACING_CAP = 1000   # backstop only; our own counters decide when to stop. Celery must not pre-empt.


def _set_status(job_id: str, payload: dict) -> None:
    """Store subtitle status and release its source claim."""
    try:
        r = get_sync_client()
        status = terminal_outbox.store_subtitles(r, job_id, payload)
        if status in TERMINAL:
            terminal_outbox.drain_one(r, job_id)
    except (RedisError, OSError) as exc:
        log.warning("subtitles_hot_write_failed")
        raise CancellationUnavailable(job_id) from exc
    try:
        release_source(r, job_id, "transcribe")
    except (RedisError, OSError):
        log.warning("source_release_failed")     # cleanup, not correctness — never let it mask the status write


def _handle_failure(task, job_id: str, exc: SttError) -> dict:
    """Retry or fail soft after an STT error."""
    err = exc.error
    if is_cancelled(job_id):
        return _finish_cancelled(job_id)
    if err.retryable:
        try:
            attempts = get_sync_client().hincrby(f"job:{job_id}", "stt_attempts", 1)
        except (RedisError, OSError) as state_exc:
            raise CancellationUnavailable(job_id) from state_exc
        if attempts <= config.stt_max_retries:
            delay = backoff_seconds(attempts - 1)
            log.warning("stt_retry_scheduled", code=err.code, attempt=attempts, retry_in=delay)
            raise task.retry(countdown=delay, max_retries=PACING_CAP)
    log.warning("subtitles_failed", code=err.code, reason=err.message)
    _set_status(job_id, {"status": "failed", "code": err.code, "reason": err.message})
    return {"status": "failed"}


def _remove_artifacts(job_dir) -> None:
    for name in ("audio.wav", "subtitles.vtt", "subs.m3u8"):
        (job_dir / name).unlink(missing_ok=True)


def _finish_cancelled(job_id: str, job_dir=None) -> dict:
    job_dir = job_dir or (config.output_dir / job_id)
    _remove_artifacts(job_dir)
    _set_status(job_id, {"status": "cancelled"})
    log.info("transcription_cancelled")
    return {"status": "cancelled"}


def _run(task, job_id: str, src: str, meta: dict) -> dict:
    if is_cancelled(job_id):
        return _finish_cancelled(job_id)
    if not has_audio(meta):
        log.info("stt_no_audio")
        _set_status(job_id, {"status": "none", "reason": "no audio stream"})
        return {"status": "none"}

    job_dir = paths.output_dir(job_id)
    wav = job_dir / "audio.wav"
    try:
        limit, window = map(int, config.stt_rate_limit.split("/"))
        try:
            decision = acquire("stt:global", limit, window)
        except (RedisError, OSError) as state_exc:
            raise CancellationUnavailable(job_id) from state_exc
        if isinstance(decision, RetryIn):
            if is_cancelled(job_id):
                return _finish_cancelled(job_id, job_dir)
            log.info("stt_rate_limited", retry_in=decision.seconds)
            raise task.retry(countdown=decision.seconds, max_retries=PACING_CAP)

        job_dir = paths.ensure_output_dir(job_id)
        wav = job_dir / "audio.wav"
        try:
            extract_audio(src, str(wav), cancelled=lambda: is_cancelled(job_id))
        except (CancellationUnavailable, JobCancelled, SoftTimeLimitExceeded):
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("audio_extract_failed", error=str(e))
            _set_status(
                job_id,
                {
                    "status": "failed",
                    "code": STT_BAD_AUDIO,
                    "reason": "audio extraction failed",
                },
            )
            return {"status": "failed"}

        if is_cancelled(job_id):
            wav.unlink(missing_ok=True)
            return _finish_cancelled(job_id, job_dir)
        segments = transcribe_audio(str(wav), cancelled=lambda: is_cancelled(job_id))
    except (JobCancelled, SttCancelled):
        return _finish_cancelled(job_id, job_dir)
    except SttError as e:
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
        try:
            return _run(self, job_id, src, meta)
        except Retry:
            raise
        except CancellationUnavailable:
            raise
        except SoftTimeLimitExceeded:
            log.warning("subtitles_timed_out")
            _remove_artifacts(config.output_dir / job_id)
            _set_status(
                job_id,
                {"status": "failed", "code": STT_UNAVAILABLE, "reason": "transcription timed out"},
            )
            return {"status": "failed"}
        except Exception:
            log.exception("subtitles_internal_error")
            _set_status(job_id, {"status": "failed", "code": STT_INTERNAL, "reason": "internal error"})
            return {"status": "failed"}
    except CancellationUnavailable as exc:
        raise self.retry(
            exc=exc,
            countdown=backoff_seconds(self.request.retries),
            max_retries=MAX_RETRIES,
        )
