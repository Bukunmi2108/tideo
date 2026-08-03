from redis.exceptions import RedisError

from app.core.logging import get_logger
from app.storage.state import get_sync_client

log = get_logger()


class JobCancelled(Exception):
    pass


class CancellationUnavailable(Exception):
    pass


def is_cancelled(job_id: str) -> bool:
    """Return whether a job is cancelled."""
    try:
        r = get_sync_client()
        return bool(
            r.exists(f"cancel:{job_id}")
            or r.hget(f"job:{job_id}", "status") == "cancelled"
        )
    except (RedisError, OSError) as exc:
        # Unknown cancellation state must stop work; committing would make cancel non-authoritative.
        log.warning("cancel_check_failed", job_id=job_id, exc_info=True)
        raise CancellationUnavailable(job_id) from exc
