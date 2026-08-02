import time
from dataclasses import dataclass
from typing import Literal, cast

from app.core.config import config
from app.storage.state import ACTIVE_DEADLINES

_RESOLVE_UPLOAD = """
local owner = redis.call('GET', KEYS[1])
if owner then
    local status = redis.call('HGET', 'job:' .. owner, 'status')
    if status == 'inspecting' or status == 'awaiting_choice' or
       status == 'queued' or status == 'transcoding' or status == 'done' then
        return {'hit', owner, status}
    end
end

redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[2])
for i = 4, #ARGV, 2 do
    redis.call('HSET', KEYS[2], ARGV[i], ARGV[i + 1])
end
redis.call('HSET', KEYS[2], 'status', 'inspecting')
redis.call('HINCRBY', KEYS[3], 'inspecting', 1)
redis.call('ZADD', KEYS[4], ARGV[3], ARGV[1])
return {'miss', ARGV[1], 'inspecting'}
"""

_INVALIDATE_DONE = """
if redis.call('GET', KEYS[1]) ~= ARGV[1] then
    return 0
end
if redis.call('HGET', KEYS[2], 'status') ~= 'done' then
    return 0
end
redis.call('DEL', KEYS[1])
return 1
"""

_RELEASE_OWNER = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

UploadOutcome = Literal["hit", "miss"]


@dataclass(frozen=True)
class UploadResolution:
    outcome: UploadOutcome
    job_id: str
    status: str


def _text(value) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def resolve_upload(
    r,
    content_hash: str,
    job_id: str,
    extra: dict,
) -> UploadResolution:
    ttl = config.output_ttl_days * 86400
    args = [value for item in extra.items() for value in (item[0], str(item[1]))]
    raw = await r.eval(
        _RESOLVE_UPLOAD,
        4,
        f"content:{content_hash}",
        f"job:{job_id}",
        "stats:active",
        ACTIVE_DEADLINES,
        job_id,
        ttl,
        time.time() + ttl,
        *args,
    )
    outcome = _text(raw[0])
    if outcome not in ("hit", "miss"):
        raise RuntimeError(f"unexpected upload resolution: {outcome}")
    return UploadResolution(
        cast(UploadOutcome, outcome),
        _text(raw[1]),
        _text(raw[2]),
    )


async def invalidate_done(r, content_hash: str, owner: str) -> bool:
    result = await r.eval(
        _INVALIDATE_DONE,
        2,
        f"content:{content_hash}",
        f"job:{owner}",
        owner,
    )
    return bool(result)


def release_owner(r, content_hash: str, owner: str) -> bool:
    return bool(r.eval(_RELEASE_OWNER, 1, f"content:{content_hash}", owner))
