from dataclasses import dataclass
from typing import Literal, cast

from app.core.config import config

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
for i = 3, #ARGV, 2 do
    redis.call('HSET', KEYS[2], ARGV[i], ARGV[i + 1])
end
redis.call('HSET', KEYS[2], 'status', 'inspecting')
redis.call('HINCRBY', KEYS[3], 'inspecting', 1)
return {'miss', ARGV[1], 'inspecting'}
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
    args = [value for item in extra.items() for value in (item[0], str(item[1]))]
    raw = await r.eval(
        _RESOLVE_UPLOAD,
        3,
        f"content:{content_hash}",
        f"job:{job_id}",
        "stats:active",
        job_id,
        config.output_ttl_days * 86400,
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
