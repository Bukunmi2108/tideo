import shutil

from app.core.config import config
from app.core.logging import get_logger

log = get_logger()


def _key(job_id: str) -> str:
    return f"src:{job_id}"


def claim_source(r, job_id: str, who: str) -> None:
    """Register a consumer of the source upload. Claimed at dispatch (before any task runs) so the
    source can't be reclaimed out from under a consumer that hasn't started yet."""
    r.sadd(_key(job_id), who)
    r.expire(_key(job_id), config.output_ttl_days * 86400)


def release_source(r, job_id: str, who: str) -> None:
    """Drop this consumer's claim; the LAST consumer reclaims the upload. The source is shared by the
    packaging task (web.mp4) and the transcribe task (audio extraction) and must outlive both — ADR-4's
    transcription routinely finishes after the ladder. A double-release just no-ops (rmtree is idempotent)."""
    removed = r.srem(_key(job_id), who)
    if removed and r.scard(_key(job_id)) == 0:   # only the consumer that drops the LAST claim reclaims
        try:
            shutil.rmtree(config.uploads_dir / job_id)
        except FileNotFoundError:
            pass
        except OSError:
            log.error("source_reclaim_failed", job_id=job_id, exc_info=True)   # recurring = a real disk problem
        r.delete(_key(job_id))
