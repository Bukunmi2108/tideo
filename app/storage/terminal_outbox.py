import json

import psycopg2
from anyio.to_thread import run_sync

from app.core.config import config
from app.core.logging import get_logger
from app.storage import db
from app.storage.state import TERMINAL_OUTBOX

log = get_logger()
BATCH_SIZE = 100
_TRANSIENT = (psycopg2.OperationalError, psycopg2.InterfaceError)

_STORE_SUBTITLES = """
redis.call('HSET', KEYS[1], 'subtitles', ARGV[2])
local status = redis.call('HGET', KEYS[1], 'status') or ''
if status == 'done' or status == 'failed' or status == 'cancelled' then
    redis.call('SADD', KEYS[2], ARGV[1])
end
return status
"""

_ACK = """
local subtitles = redis.call('HGET', KEYS[1], 'subtitles')
if ARGV[2] == '0' then
    if subtitles then
        return 0
    end
elseif subtitles ~= ARGV[3] then
    return 0
end
redis.call('SREM', KEYS[2], ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[4])
return 1
"""


def store_subtitles(r, job_id: str, payload: dict) -> str:
    status = r.eval(
        _STORE_SUBTITLES,
        2,
        f"job:{job_id}",
        TERMINAL_OUTBOX,
        job_id,
        json.dumps(payload),
    )
    return status.decode() if isinstance(status, bytes) else str(status)


def _ack_args(rec: dict, job_id: str) -> tuple[str, str, str, int]:
    return (
        job_id,
        "1" if "subtitles" in rec else "0",
        rec.get("subtitles") or "",
        config.output_ttl_days * 86400,
    )


def drain_one(r, job_id: str) -> bool:
    rec = r.hgetall(f"job:{job_id}")
    if not rec:
        raise RuntimeError(f"terminal projection payload missing for job {job_id}")
    results = json.loads(rec.get("rendition_results") or "null")
    try:
        db.persist_terminal(job_id, rec, results=results)
    except _TRANSIENT:
        log.exception("terminal_projection_failed", job_id=job_id)
        return False

    try:
        r.eval(
            _ACK,
            2,
            f"job:{job_id}",
            TERMINAL_OUTBOX,
            *_ack_args(rec, job_id),
        )
    except Exception:
        log.exception("terminal_projection_ack_failed", job_id=job_id)
    return True


async def adrain_one(r, job_id: str) -> bool:
    rec = await r.hgetall(f"job:{job_id}")
    if not rec:
        raise RuntimeError(f"terminal projection payload missing for job {job_id}")
    results = json.loads(rec.get("rendition_results") or "null")
    try:
        await run_sync(lambda: db.persist_terminal(job_id, rec, results=results))
    except _TRANSIENT:
        log.exception("terminal_projection_failed", job_id=job_id)
        return False

    try:
        await r.eval(
            _ACK,
            2,
            f"job:{job_id}",
            TERMINAL_OUTBOX,
            *_ack_args(rec, job_id),
        )
    except Exception:
        log.exception("terminal_projection_ack_failed", job_id=job_id)
    return True


def drain(r, limit: int = BATCH_SIZE) -> int:
    cursor = 0
    pending = []
    while len(pending) < limit:
        cursor, page = r.sscan(TERMINAL_OUTBOX, cursor=cursor, count=limit - len(pending))
        pending.extend(page)
        if cursor == 0:
            break

    projected = 0
    for job_id in pending:
        try:
            if not drain_one(r, job_id):
                break
        except Exception:
            log.exception("terminal_projection_invalid", job_id=job_id)
            continue
        projected += 1
    return projected
