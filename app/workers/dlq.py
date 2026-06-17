import json

from app.core.logging import get_logger

log = get_logger()

DLQ_KEY = "dlq"


def add(client, rec: dict) -> None:
    """Write a dead-letter record. Fail-open: a DLQ miss is an ops gap, not a correctness bug
    (the job hash already carries the error)."""
    try:
        client.hset(DLQ_KEY, mapping={rec["id"]: json.dumps(rec)})
    except Exception:
        log.warning("dlq_write_failed", dlq_id=rec.get("id"))
