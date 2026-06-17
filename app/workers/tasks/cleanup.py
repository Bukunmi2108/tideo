import logging
import shutil
from datetime import datetime, timedelta, timezone

from app.core.config import config
from app.events.producer import emit
from app.events.topics import JOB_EXPIRED
from app.storage import db
from app.storage.state import get_sync_client
from app.workers.base import CleanupTask
from app.workers.celery_app import app

logger = logging.getLogger(__name__)


def _expire_outputs(now: datetime) -> tuple[int, int]:
    """Expire done jobs past the retention window. Delete-before-mark: a job is marked expired only after
    its bytes are gone, so a deletion failure leaves it eligible for retry instead of silently leaking.
    mark_expired's done-guard makes re-runs idempotent. Returns (expired, failed)."""
    cutoff = now - timedelta(days=config.output_ttl_days)
    r = get_sync_client()
    expired = failed = 0
    for row in db.list_expirable(cutoff):
        job_id = row["job_id"]
        try:
            try:
                shutil.rmtree(config.output_dir / job_id)
            except FileNotFoundError:
                pass                                     # already gone (idempotent re-run) — proceed to mark
            if row.get("content_hash"):
                r.delete(f"content:{row['content_hash']}")   # a dedupe key pointing at deleted files is a bug
            r.delete(f"job:{job_id}")                    # drop stale hot state -> reads fall back to PG (expired)
            if db.mark_expired(job_id, now):
                emit(JOB_EXPIRED, job_id, {})
                expired += 1
        except Exception:
            failed += 1
            logger.exception("expire failed job=%s — left eligible for retry", job_id)
    if failed:
        logger.error("expiry sweep: %d job(s) could not be reclaimed this run", failed)
    return expired, failed


def _sweep_stale_sources(now: datetime) -> int:
    """Reclaim source uploads of failed/cancelled jobs past the grace window (success deletes its own)."""
    cutoff = now - timedelta(seconds=config.source_grace_seconds)
    removed = 0
    for row in db.list_stale_sources(cutoff):
        src_dir = config.uploads_dir / row["job_id"]
        if not src_dir.exists():
            continue
        try:
            shutil.rmtree(src_dir)
            removed += 1
        except OSError:
            logger.warning("source reclaim failed job=%s (still on disk)", row["job_id"])
    return removed


def _sweep_temp_dirs(now: datetime) -> int:
    """Collect orphaned atomic_dir/atomic_path temps (hard-kill leftovers), older than any possible encode."""
    threshold = config.transcode_max_seconds + 60
    if not config.output_dir.exists():
        return 0
    removed = 0
    for job_dir in config.output_dir.iterdir():
        if not job_dir.is_dir():
            continue
        for child in job_dir.iterdir():
            if not (child.name.endswith(".tmp") or ".tmp." in child.name):   # atomic_dir / atomic_path temps
                continue
            try:
                if now.timestamp() - child.stat().st_mtime <= threshold:
                    continue                             # could be a live encode's temp — leave it
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
                removed += 1
            except FileNotFoundError:
                continue                                 # vanished mid-iteration (an atomic rename) — benign
            except OSError:
                logger.warning("temp sweep: could not remove %s", child)
    return removed


@app.task(base=CleanupTask)
def sweep() -> dict:
    """Storage lifecycle sweep: expire done outputs past TTL, reclaim failed/cancelled sources, collect
    orphaned temps. Beat-scheduled and run once at boot (Beat is silent while a sleeping Space is down)."""
    now = datetime.now(timezone.utc)
    expired, failed = _expire_outputs(now)
    result = {"expired": expired, "failed": failed,
              "sources": _sweep_stale_sources(now), "temps": _sweep_temp_dirs(now)}
    logger.info("cleanup sweep %s", result)
    return result
