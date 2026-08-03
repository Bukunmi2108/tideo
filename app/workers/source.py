import shutil

from app.core.config import config
from app.core.logging import get_logger

log = get_logger()
RECLAIM_MARKER = "__reclaim__"

_CLAIM_SOURCE = """
redis.call('SREM', KEYS[1], ARGV[1])
for i = 2, #ARGV - 1 do
    redis.call('SADD', KEYS[1], ARGV[i])
end
redis.call('EXPIRE', KEYS[1], ARGV[#ARGV])
return redis.call('SCARD', KEYS[1])
"""

_RELEASE_SOURCE = """
if redis.call('SREM', KEYS[1], ARGV[1]) == 0 then
    return 0
end
if redis.call('SCARD', KEYS[1]) == 0 then
    redis.call('SADD', KEYS[1], ARGV[2])
    redis.call('EXPIRE', KEYS[1], ARGV[3])
    return 1
end
return 0
"""


def _key(job_id: str) -> str:
    return f"src:{job_id}"


def claim_source(r, job_id: str, *consumers: str) -> None:
    """Atomically reserve every source consumer and the claim TTL."""
    r.eval(
        _CLAIM_SOURCE,
        1,
        _key(job_id),
        RECLAIM_MARKER,
        *consumers,
        config.output_ttl_days * 86400,
    )


def release_source(r, job_id: str, consumer: str) -> None:
    """Reclaim the upload after the last reserved consumer releases it."""
    reclaim = r.eval(
        _RELEASE_SOURCE,
        1,
        _key(job_id),
        consumer,
        RECLAIM_MARKER,
        config.output_ttl_days * 86400,
    )
    if not reclaim:
        return
    try:
        shutil.rmtree(config.uploads_dir / job_id)
    except FileNotFoundError:
        pass
    except OSError:
        log.exception("source_reclaim_failed", job_id=job_id)
        return
    r.delete(_key(job_id))
