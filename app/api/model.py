import json
import logging

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class JobResponse(BaseModel):
    job_id: str
    status: str
    source: dict | None = None
    recommended_presets: list[str] | None = None
    web_safe: bool | None = None
    web_safe_reason: str | None = None
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
        logger.warning("malformed %s for job=%s; omitting", field, job_id)
        return None


def results_view(job_id: str, rec: dict) -> dict:
    """Artifact URL set for a `done` job, by route convention. No disk read; degrades to []/None on missing metadata."""
    presets = _safe_loads(rec.get("presets"), "presets", job_id) or []
    sm = _safe_loads(rec.get("source_meta"), "source_meta", job_id)
    duration = sm.get("duration") if isinstance(sm, dict) else None
    return {
        "playlist": f"/jobs/{job_id}/playlist",
        "web_mp4": f"/jobs/{job_id}/file",
        "poster": f"/jobs/{job_id}/poster",
        "sprite": f"/jobs/{job_id}/sprite",
        "player": f"/jobs/{job_id}/player",
        "presets": presets,
        "duration": duration,
    }