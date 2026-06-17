import json

from pydantic import BaseModel

from app.core.logging import get_logger

log = get_logger()


class JobResponse(BaseModel):
    job_id: str
    status: str
    source: dict | None = None
    source_filename: str | None = None
    recommended_presets: list[str] | None = None
    web_safe: bool | None = None
    web_safe_reason: str | None = None
    presets: list[str] | None = None
    progress: dict | None = None
    results: dict | None = None
    error: dict | None = None


def progress_map(rec: dict) -> dict[str, float]:
    """Extract `progress:{preset}` hash fields into {preset: percent}. Shared by GET and the WS snapshot."""
    return {
        k.split(":", 1)[1]: float(v)
        for k, v in rec.items()
        if k.startswith("progress:")
    }


def _safe_loads(raw: str | None, field: str, job_id: str):
    """Parse a JSON hash field, tolerating absence and corruption — a torn hash must not 500 a status read."""
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        log.warning("malformed_field", field=field, job_id=job_id)
        return None


def _results_payload(job_id: str, presets: list, duration, subtitles=None) -> dict:
    """Artifact URL set for a `done` job, by route convention. No disk read.
    `subtitles` is the {status,...} the transcribe task records (None if captions weren't requested)."""
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


def results_view(job_id: str, rec: dict) -> dict:
    """From the hot Redis hash; degrades to []/None on missing metadata."""
    presets = _safe_loads(rec.get("presets"), "presets", job_id) or []
    sm = _safe_loads(rec.get("source_meta"), "source_meta", job_id)
    duration = sm.get("duration") if isinstance(sm, dict) else None
    return _results_payload(job_id, presets, duration,
                            _safe_loads(rec.get("subtitles"), "subtitles", job_id))


def results_view_pg(job_id: str, row: dict) -> dict:
    """From a cold Postgres row (presets already a list; duration a NUMERIC -> float; subtitles a JSONB dict)."""
    duration = row.get("source_duration_s")
    return _results_payload(job_id, row.get("presets") or [],
                            float(duration) if duration is not None else None,
                            row.get("subtitles"))


class JobSummary(BaseModel):
    job_id: str
    status: str
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