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
    if not isinstance(env, dict) or any(
        not isinstance(env.get(key), str) or not env[key].strip() for key in REQUIRED
    ):
        raise BadEvent("missing or invalid required envelope fields")
    return env


def process(env: dict, *, claim, enqueue, release) -> str:
    if env["event_type"] != JOB_CREATED:
        return "skipped"
    if not claim(env["event_id"]):
        return "duplicate"
    try:
        enqueue(env)
    except Exception:
        release(env["event_id"])
        raise
    return "dispatched"
