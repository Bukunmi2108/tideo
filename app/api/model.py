import json
import math
from typing import Literal

from pydantic import BaseModel

from app.core.logging import get_logger
from app.domain.errors import is_retryable

log = get_logger()

JobStatus = Literal[
    "inspecting",
    "awaiting_choice",
    "queued",
    "transcoding",
    "done",
    "failed",
    "cancelled",
    "expired",
]


class SourceMeta(BaseModel):
    container: str
    video_codec: str | None
    audio_codec: str | None
    width: int
    height: int
    duration: float
    bitrate: int | None
    fps: float | None
    has_audio: bool
    video_streams: int
    audio_streams: int


class JobError(BaseModel):
    code: str
    message: str
    stage: str | None
    retryable: bool


class SubtitleStatus(BaseModel):
    status: Literal["processing", "ready", "failed", "none"]
    url: str | None = None
    reason: str | None = None
    code: str | None = None


class JobResults(BaseModel):
    playlist: str
    web_mp4: str
    poster: str
    sprite: str
    player: str
    presets: list[str]
    duration: float | None
    subtitles: SubtitleStatus | None = None


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    source: SourceMeta | None = None
    source_filename: str | None = None
    recommended_presets: list[str] | None = None
    web_safe: bool | None = None
    web_safe_reason: str | None = None
    presets: list[str] | None = None
    progress: dict[str, float] | None = None
    results: JobResults | None = None
    error: JobError | None = None


def _malformed(field: str, job_id: str) -> None:
    log.warning("malformed_field", field=field, job_id=job_id)


def _safe_loads(raw, field: str, job_id: str):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        _malformed(field, job_id)
        return None


def _string_list(value, field: str, job_id: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list) and all(isinstance(item, str) and item for item in value):
        return value
    _malformed(field, job_id)
    return []


def _object(value, field: str, job_id: str) -> dict | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    _malformed(field, job_id)
    return None


def json_list(rec: dict, field: str, job_id: str) -> list[str]:
    return _string_list(_safe_loads(rec.get(field), field, job_id), field, job_id)


def _json_object(rec: dict, field: str, job_id: str) -> dict | None:
    return _object(_safe_loads(rec.get(field), field, job_id), field, job_id)


def _finite_float(value, field: str, job_id: str) -> float | None:
    if value is None or isinstance(value, bool):
        if value is not None:
            _malformed(field, job_id)
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        _malformed(field, job_id)
        return None
    if not math.isfinite(number):
        _malformed(field, job_id)
        return None
    return number


def progress_map(rec: dict, job_id: str) -> dict[str, float]:
    progress = {}
    for key, raw in rec.items():
        if not key.startswith("progress:"):
            continue
        preset = key.partition(":")[2]
        value = _finite_float(raw, key, job_id)
        if not preset or value is None or not 0 <= value <= 100:
            if value is not None:
                _malformed(key, job_id)
            continue
        progress[preset] = value
    return progress


def error_view(rec: dict) -> dict:
    code = rec.get("error_code") or "UNKNOWN"
    return {
        "code": code,
        "message": rec.get("error_message") or "job failed",
        "stage": rec.get("error_stage") or None,
        "retryable": is_retryable(code),
    }


def _results_payload(
    job_id: str,
    presets: list[str],
    duration: float | None,
    subtitles: dict | None,
) -> dict:
    return {
        "playlist": f"/jobs/{job_id}/playlist",
        "web_mp4": f"/jobs/{job_id}/file",
        "poster": f"/jobs/{job_id}/poster",
        "sprite": f"/jobs/{job_id}/sprite",
        "player": f"/jobs/{job_id}/player",
        "presets": presets,
        "duration": duration,
        "subtitles": subtitles,
    }


def _subtitles(value, job_id: str) -> dict | None:
    value = _object(value, "subtitles", job_id)
    if value is None:
        return None
    if value.get("status") in {"processing", "ready", "failed", "none"}:
        return value
    _malformed("subtitles", job_id)
    return None


def results_view(job_id: str, rec: dict) -> dict:
    presets = json_list(rec, "presets", job_id)
    source = _json_object(rec, "source_meta", job_id)
    duration = (
        _finite_float(source.get("duration"), "source_meta.duration", job_id)
        if source
        else None
    )
    subtitles = _subtitles(_safe_loads(rec.get("subtitles"), "subtitles", job_id), job_id)
    return _results_payload(job_id, presets, duration, subtitles)


def results_view_pg(job_id: str, row: dict) -> dict:
    presets = _string_list(row.get("presets"), "presets", job_id)
    duration = _finite_float(row.get("source_duration_s"), "source_duration_s", job_id)
    subtitles = _subtitles(row.get("subtitles"), job_id)
    return _results_payload(job_id, presets, duration, subtitles)


class JobSummary(BaseModel):
    job_id: str
    status: JobStatus
    source_filename: str | None = None
    duration: float | None = None
    created_at: str | None = None
    finished_at: str | None = None
    expires_at: str | None = None
    poster: str | None = None


class JobListResponse(BaseModel):
    items: list[JobSummary]
    limit: int
    offset: int
    has_more: bool
