import json
from dataclasses import dataclass
from typing import Literal, cast

from app.domain.state import ACTIVE, transition
from app.storage.state import EVENT_OUTBOX

_TRANSITION = """
local current = redis.call('HGET', KEYS[1], 'status')
if current ~= ARGV[1] then
    return {0, current or ''}
end

redis.call('HSET', KEYS[1], 'status', ARGV[2])
for i = 5, #ARGV, 2 do
    redis.call('HSET', KEYS[1], ARGV[i], ARGV[i + 1])
end
if ARGV[3] == '1' then
    redis.call('HINCRBY', KEYS[2], ARGV[1], -1)
end
if ARGV[4] == '1' then
    redis.call('HINCRBY', KEYS[2], ARGV[2], 1)
end
return {1, ARGV[2]}
"""

_QUEUE_JOB = """
local current = redis.call('HGET', KEYS[1], 'status')
if current ~= 'awaiting_choice' then
    return {0, current or ''}
end

redis.call('HSET', KEYS[1],
    'status', 'queued',
    'presets', ARGV[1],
    'subtitles', ARGV[2])
redis.call('HINCRBY', KEYS[2], 'awaiting_choice', -1)
redis.call('HINCRBY', KEYS[2], 'queued', 1)
redis.call('HSET', KEYS[3], ARGV[3], ARGV[4])
return {1, 'queued'}
"""

_RESERVE_DISPATCH = """
local status = redis.call('HGET', KEYS[1], 'status')
if not status then
    return {'missing', ''}
end
if status ~= 'queued' or redis.call('EXISTS', KEYS[2]) == 1 then
    return {'skipped', status}
end

local existing_event = redis.call('HGET', KEYS[1], 'dispatch_event_id')
if existing_event then
    local outcome = 'existing'
    if existing_event ~= ARGV[1] then
        outcome = 'conflict'
    end
    return {
        outcome,
        status,
        existing_event,
        redis.call('HGET', KEYS[1], 'rendition_ids') or '[]',
        redis.call('HGET', KEYS[1], 'chord_callback_id') or '',
        redis.call('HGET', KEYS[1], 'transcribe_id') or ''
    }
end

redis.call('HSET', KEYS[1],
    'dispatch_event_id', ARGV[1],
    'rendition_ids', ARGV[2],
    'chord_callback_id', ARGV[3],
    'transcribe_id', ARGV[4])
return {'reserved', status, ARGV[1], ARGV[2], ARGV[3], ARGV[4]}
"""

_CANCEL_JOB = """
local status = redis.call('HGET', KEYS[1], 'status')
if not status then
    return {'missing', ''}
end
if status ~= 'queued' and status ~= 'transcoding' then
    return {'wrong_state', status}
end

redis.call('HSET', KEYS[1], 'status', 'cancelled')
redis.call('HINCRBY', KEYS[2], status, -1)
redis.call('SET', KEYS[3], '1', 'EX', ARGV[1])
redis.call('EXPIRE', KEYS[1], ARGV[1])
return {
    'cancelled',
    status,
    redis.call('HGET', KEYS[1], 'rendition_ids') or '[]',
    redis.call('HGET', KEYS[1], 'chord_callback_id') or '',
    redis.call('HGET', KEYS[1], 'transcribe_id') or ''
}
"""


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def _extra_args(extra: dict | None) -> list[str]:
    args: list[str] = []
    for key, value in (extra or {}).items():
        args.extend((key, str(value)))
    return args


def _transition_args(current: str, target: str, extra: dict | None) -> list[str]:
    return [
        current,
        target,
        "1" if current in ACTIVE else "0",
        "1" if target in ACTIVE else "0",
        *_extra_args(extra),
    ]


def transition_status(r, job_id: str, target: str, *, caller: str, extra: dict | None = None) -> str | None:
    """Atomically transition a job."""
    for _ in range(8):
        current = _text(r.hget(f"job:{job_id}", "status") or "")
        nxt = transition(current, target, job_id=job_id, caller=caller)
        if nxt is None:
            return None
        result = r.eval(
            _TRANSITION,
            2,
            f"job:{job_id}",
            "stats:active",
            *_transition_args(current, target, extra),
        )
        if int(result[0]) == 1:
            return target
    raise RuntimeError(f"transition contention did not settle for job {job_id}")


async def atransition_status(
    r,
    job_id: str,
    target: str,
    *,
    caller: str,
    extra: dict | None = None,
    expected: str | None = None,
) -> str | None:
    """Atomically transition a job with async Redis."""
    if expected is not None:
        nxt = transition(expected, target, job_id=job_id, caller=caller)
        if nxt is None:
            return None
        result = await r.eval(
            _TRANSITION,
            2,
            f"job:{job_id}",
            "stats:active",
            *_transition_args(expected, target, extra),
        )
        return target if int(result[0]) == 1 else None
    for _ in range(8):
        current = _text(await r.hget(f"job:{job_id}", "status") or "")
        nxt = transition(current, target, job_id=job_id, caller=caller)
        if nxt is None:
            return None
        result = await r.eval(
            _TRANSITION,
            2,
            f"job:{job_id}",
            "stats:active",
            *_transition_args(current, target, extra),
        )
        if int(result[0]) == 1:
            return target
    raise RuntimeError(f"transition contention did not settle for job {job_id}")


async def aqueue_job(
    r,
    job_id: str,
    *,
    presets: str,
    subtitles: bool,
    event_id: str,
    event_json: str,
) -> str | None:
    """Atomically queue a job and retain its dispatch-triggering event until Kafka acknowledges it."""
    transition("awaiting_choice", "queued", job_id=job_id, caller="transcode")
    result = await r.eval(
        _QUEUE_JOB,
        3,
        f"job:{job_id}",
        "stats:active",
        EVENT_OUTBOX,
        presets,
        "true" if subtitles else "false",
        event_id,
        event_json,
    )
    return "queued" if int(result[0]) == 1 else None


@dataclass(frozen=True)
class DispatchPlan:
    event_id: str
    rendition_ids: tuple[str, ...]
    callback_id: str
    transcribe_id: str | None = None


ReserveOutcome = Literal["reserved", "existing", "conflict", "skipped", "missing"]


@dataclass(frozen=True)
class DispatchReservation:
    outcome: ReserveOutcome
    status: str | None
    plan: DispatchPlan | None


def reserve_dispatch(r, job_id: str, proposed: DispatchPlan) -> DispatchReservation:
    raw = r.eval(
        _RESERVE_DISPATCH,
        2,
        f"job:{job_id}",
        f"cancel:{job_id}",
        proposed.event_id,
        json.dumps(proposed.rendition_ids),
        proposed.callback_id,
        proposed.transcribe_id or "",
    )
    outcome = _text(raw[0])
    if outcome not in ("reserved", "existing", "conflict", "skipped", "missing"):
        raise RuntimeError(f"unexpected dispatch reservation outcome: {outcome}")
    typed_outcome = cast(ReserveOutcome, outcome)
    status = _text(raw[1]) or None
    if len(raw) < 6:
        return DispatchReservation(typed_outcome, status, None)
    plan = DispatchPlan(
        event_id=_text(raw[2]),
        rendition_ids=tuple(json.loads(_text(raw[3]))),
        callback_id=_text(raw[4]),
        transcribe_id=_text(raw[5]) or None,
    )
    return DispatchReservation(typed_outcome, status, plan)


CancelOutcome = Literal["cancelled", "wrong_state", "missing"]


@dataclass(frozen=True)
class CancelResult:
    outcome: CancelOutcome
    previous_status: str | None
    task_ids: tuple[str, ...] = ()


async def acancel_job(r, job_id: str, *, ttl: int) -> CancelResult:
    raw = await r.eval(
        _CANCEL_JOB,
        3,
        f"job:{job_id}",
        "stats:active",
        f"cancel:{job_id}",
        ttl,
    )
    outcome = _text(raw[0])
    if outcome not in ("cancelled", "wrong_state", "missing"):
        raise RuntimeError(f"unexpected cancellation outcome: {outcome}")
    typed_outcome = cast(CancelOutcome, outcome)
    previous = _text(raw[1]) or None
    if len(raw) < 5:
        return CancelResult(typed_outcome, previous)
    ids = list(json.loads(_text(raw[2])))
    ids.extend(value for value in (_text(raw[3]), _text(raw[4])) if value)
    return CancelResult(typed_outcome, previous, tuple(ids))
