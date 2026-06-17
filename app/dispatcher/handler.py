import json
from app.events.topics import JOB_CREATED

class BadEvent(Exception):
    pass

REQUIRED = ("event_id", "event_type", "job_id")

def parse_event(raw: bytes | None) -> dict:
    if raw is None:
        raise BadEvent("empty message value")
    try:
        env = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as e:
        raise BadEvent(f"undeserializable: {e}") from e
    if not isinstance(env, dict) or any(k not in env for k in REQUIRED):
        raise BadEvent("missing required envelope fields")
    return env

def is_dispatchable(env: dict) -> bool:
    return env["event_type"] == JOB_CREATED


def process(env: dict, *, claim, enqueue, release=lambda _id: None) -> str:
    """Decide + act on one parsed event. Pure of Kafka — claim/enqueue/release are injected so this is
    unit-testable. Returns the action taken; the caller commits the offset after it returns.

    claim(event_id) -> bool may raise RedisError; the caller turns that into fail-closed stall.
    enqueue receives the whole envelope. If it raises (e.g. broker down), release the claim so the
    re-consumed event retries cleanly instead of being skipped as a duplicate; then re-raise to stall.
    """
    if not is_dispatchable(env):
        return "skipped"
    if not claim(env["event_id"]):
        return "duplicate"
    try:
        enqueue(env)
    except Exception:
        release(env["event_id"])
        raise
    return "dispatched"