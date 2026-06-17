import os
import time
from dataclasses import dataclass

from app.storage.state import get_sync_client


@dataclass(frozen=True)
class Allowed:
    pass


@dataclass(frozen=True)
class RetryIn:
    seconds: float


def parse_rate(spec: str) -> tuple[int, int]:
    """'3/60' -> (3 calls, 60 second window)."""
    limit, window = spec.split("/")
    return int(limit), int(window)


def decide(timestamps_ms: list[int], now_ms: int, window_ms: int, limit: int) -> Allowed | RetryIn:
    """Pure sliding-window decision over the live timestamps in the window. A sorted-set window (every
    entry timestamped) has no fixed-bucket boundary burst — two bucket-widths of calls can't straddle an
    edge and double the rate. Returns RetryIn(seconds until the oldest in-window entry ages out)."""
    live = [t for t in timestamps_ms if t > now_ms - window_ms]
    if len(live) < limit:
        return Allowed()
    return RetryIn(round(max(0, min(live) + window_ms - now_ms) / 1000, 3))


# Atomic check-and-take: trim the window, count, and either ZADD+allow or report the wait — in one
# round-trip so concurrent acquirers can't both read "under limit" and both take the last slot.
_LUA = """
local key, now, window, limit = KEYS[1], tonumber(ARGV[1]), tonumber(ARGV[2]), tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
if redis.call('ZCARD', key) < limit then
  redis.call('ZADD', key, now, ARGV[4])
  redis.call('PEXPIRE', key, window)
  return -1
end
local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
local wait = tonumber(oldest[2]) + window - now
if wait < 0 then wait = 0 end
return wait
"""


def acquire(key: str, limit: int, window_seconds: int) -> Allowed | RetryIn:
    """Take a slot for `key` if the window has room, else RetryIn(seconds until one frees)."""
    now_ms = int(time.time() * 1000)
    member = f"{now_ms}-{os.urandom(4).hex()}"     # unique so same-ms acquires don't collide on the score key
    wait_ms = get_sync_client().eval(_LUA, 1, key, now_ms, window_seconds * 1000, limit, member)
    if wait_ms == -1:
        return Allowed()
    return RetryIn(round(int(wait_ms) / 1000, 3))
