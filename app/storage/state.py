import redis
import redis.asyncio as aioredis
from app.core.config import config
from app.domain.state import ACTIVE

_async = aioredis.Redis(host=config.redis_host, port=config.redis_port, decode_responses=True)
_sync = redis.Redis(host=config.redis_host, port=config.redis_port, decode_responses=True)

ACTIVE_COUNTS = "stats:active"   # hash {state: count} — only the 4 active states; terminals counted in Postgres


def get_client() -> aioredis.Redis:
    return _async

def get_sync_client() -> redis.Redis:
    return _sync


def write_status(r, job_id: str, status: str, extra: dict | None = None) -> None:
    """Set job status (+ extra fields) and maintain the active-state counter. The single choke point
    for status writes, so the counter can't drift from a forgotten call site. The counter drains to ~0
    when idle, so a crash between the hset and the hincrby self-corrects."""
    key = f"job:{job_id}"
    old = r.hget(key, "status")
    r.hset(key, mapping={"status": status, **(extra or {})})
    if old in ACTIVE:
        r.hincrby(ACTIVE_COUNTS, old, -1)
    if status in ACTIVE:
        r.hincrby(ACTIVE_COUNTS, status, 1)


async def awrite_status(r, job_id: str, status: str, extra: dict | None = None) -> None:
    key = f"job:{job_id}"
    old = await r.hget(key, "status")
    await r.hset(key, mapping={"status": status, **(extra or {})})
    if old in ACTIVE:
        await r.hincrby(ACTIVE_COUNTS, old, -1)
    if status in ACTIVE:
        await r.hincrby(ACTIVE_COUNTS, status, 1)