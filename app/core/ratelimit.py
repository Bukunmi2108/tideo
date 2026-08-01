import os
from dataclasses import dataclass

from app.storage.state import get_sync_client


class Allowed:
    pass


@dataclass(frozen=True)
class RetryIn:
    seconds: float


# Redis time keeps the sliding window consistent across workers with different clocks.
_LUA = """
local key, window, limit = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2])
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
if redis.call('ZCARD', key) < limit then
  redis.call('ZADD', key, now, ARGV[3])
  redis.call('PEXPIRE', key, window)
  return -1
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local wait = tonumber(oldest[2]) + window - now
if wait < 0 then wait = 0 end
return wait
"""


def acquire(key: str, limit: int, window_seconds: int) -> Allowed | RetryIn:
    if limit < 1 or window_seconds < 1:
        raise ValueError("limit and window_seconds must be positive")
    wait_ms = get_sync_client().eval(
        _LUA,
        1,
        key,
        window_seconds * 1000,
        limit,
        os.urandom(8).hex(),
    )
    if wait_ms == -1:
        return Allowed()
    return RetryIn(round(int(wait_ms) / 1000, 3))
