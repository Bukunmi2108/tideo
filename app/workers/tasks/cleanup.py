import shutil
from datetime import UTC, datetime, timedelta
from typing import cast

from app.core.config import config
from app.core.logging import get_logger
from app.domain.errors import JOB_STALE, LIFECYCLE
from app.domain.state import ACTIVE
from app.events.producer import emit
from app.events.topics import JOB_EXPIRED
from app.storage import db, dedupe, terminal_outbox
from app.storage.job_control import transition_status
from app.storage.state import ACTIVE_DEADLINES, get_sync_client
from app.workers.base import CleanupTask
from app.workers.celery_app import app

log = get_logger()
ACTIVE_BATCH_SIZE = 100


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
                dedupe.release_owner(r, row["content_hash"], job_id)
            r.delete(f"job:{job_id}")                    # drop stale hot state -> reads fall back to PG (expired)
            if db.mark_expired(job_id, now):
                emit(JOB_EXPIRED, job_id, {})
                expired += 1
        except Exception:
            failed += 1
            log.exception("expire_failed", job_id=job_id)
    if failed:
        log.error("expiry_reclaim_incomplete", failed=failed)
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
            log.warning("source_reclaim_failed", job_id=row["job_id"], scope="sweep")
    return removed


def _sweep_stale_active(now: datetime) -> int:
    r = get_sync_client()
    due = cast(
        list[str],
        r.zrangebyscore(
            ACTIVE_DEADLINES,
            "-inf",
            now.timestamp(),
            start=0,
            num=ACTIVE_BATCH_SIZE,
        ),
    )
    failed = 0
    for job_id in due:
        rec = r.hgetall(f"job:{job_id}")
        if not rec or rec.get("status") not in ACTIVE:
            r.zrem(ACTIVE_DEADLINES, job_id)
            continue
        nxt = transition_status(
            r,
            job_id,
            "failed",
            caller="cleanup",
            extra={
                "error_code": JOB_STALE,
                "error_message": "job exceeded the active retention window",
                "error_stage": LIFECYCLE,
            },
        )
        if not nxt:
            continue
        r.set(f"cancel:{job_id}", "1", ex=config.output_ttl_days * 86400)
        terminal_outbox.drain_one(r, job_id)
        content_hash = cast(str | None, rec.get("content_hash"))
        if content_hash:
            dedupe.release_owner(r, content_hash, job_id)
        for path in (config.uploads_dir / job_id, config.output_dir / job_id):
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                pass
            except OSError:
                log.warning("stale_active_reclaim_failed", job_id=job_id, path=str(path))
        failed += 1
    return failed


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
                log.warning("temp_sweep_failed", path=str(child))
    return removed


@app.task(base=CleanupTask)
def drain_terminal() -> dict:
    projected = terminal_outbox.drain(get_sync_client())
    return {"projected": projected}


@app.task(base=CleanupTask)
def sweep() -> dict:
    """Run the recoverable storage lifecycle work, including the boot-time catch-up path."""
    now = datetime.now(UTC)
    expired, failed = _expire_outputs(now)
    result = {"projected": terminal_outbox.drain(get_sync_client()),
              "expired": expired, "failed": failed,
              "stale_active": _sweep_stale_active(now),
              "sources": _sweep_stale_sources(now), "temps": _sweep_temp_dirs(now)}
    log.info("cleanup_sweep_completed", **result)
    return result
