from typing import cast
from app.domain.state import transition
from app.events.producer import emit
from app.events.topics import JOB_COMPLETED
from app.storage.state import get_sync_client
from app.workers.base import PackageTask
from app.workers.celery_app import app


@app.task(base=PackageTask)
def noop() -> str:
    return "package ok"


@app.task(base=PackageTask)
def package(results, job_id: str) -> dict:
    """Chord callback. Fires once all renditions succeed (chord prepends their results).
    4.3: mark the job done + emit job.completed. 4.4 replaces the body with master.m3u8 assembly."""
    r = get_sync_client()
    cur = cast(str, r.hget(f"job:{job_id}", "status")) or ""
    nxt = transition(cur, "done", job_id=job_id, caller="package")
    if nxt:
        r.hset(f"job:{job_id}", mapping={"status": nxt})
        emit(JOB_COMPLETED, job_id, {"renditions": len(results or [])})
    return {"status": nxt, "job_id": job_id}
