import redis
import redis.asyncio as aioredis

from app.core.config import config

_async = aioredis.Redis(host=config.redis_host, port=config.redis_port, decode_responses=True)
_sync = redis.Redis(host=config.redis_host, port=config.redis_port, decode_responses=True)
ACTIVE_COUNTS = "stats:active"
ACTIVE_DEADLINES = "jobs:active:deadlines"
EVENT_OUTBOX = "events:outbox"
TERMINAL_OUTBOX = "jobs:terminal:outbox"


def get_client() -> aioredis.Redis:
    return _async

def get_sync_client() -> redis.Redis:
    return _sync
