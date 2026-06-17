import json
import logging

logger = logging.getLogger(__name__)

DLQ_KEY = "dlq"


def add(client, rec: dict) -> None:
    """Write a dead-letter record. Fail-open: a DLQ miss is an ops gap, not a correctness bug
    (the job hash already carries the error)."""
    try:
        client.hset(DLQ_KEY, mapping={rec["id"]: json.dumps(rec)})
    except Exception:
        logger.warning("dlq write failed id=%s (continuing)", rec.get("id"))
