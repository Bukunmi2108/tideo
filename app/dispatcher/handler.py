import json
import math
from uuid import UUID

from app.events.envelope import SCHEMA_VERSION
from app.events.topics import JOB_CREATED


class BadEvent(Exception):
    pass


REQUIRED_TEXT = ("event_id", "event_type", "job_id", "timestamp", "producer")


def _validate_created_payload(payload: dict) -> None:
    presets = payload.get("presets")
    subtitles = payload.get("subtitles")
    duration = payload.get("source_duration")
    if (
        not isinstance(presets, list)
        or not presets
        or any(not isinstance(preset, str) or not preset.strip() for preset in presets)
        or len(set(presets)) != len(presets)
        or not isinstance(subtitles, bool)
        or isinstance(duration, bool)
        or not isinstance(duration, (int, float))
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise BadEvent("invalid job.created payload")


def parse_event(raw: bytes | None) -> dict:
    if raw is None:
        raise BadEvent("empty message value")
    try:
        env = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as e:
        raise BadEvent(f"undeserializable: {e}") from e
    if not isinstance(env, dict) or any(
        not isinstance(env.get(key), str) or not env[key].strip() for key in REQUIRED_TEXT
    ):
        raise BadEvent("missing or invalid required envelope fields")
    try:
        UUID(env["event_id"])
    except ValueError as exc:
        raise BadEvent("invalid event_id") from exc
    if type(env.get("schema_version")) is not int or env["schema_version"] != SCHEMA_VERSION:
        raise BadEvent("unsupported schema_version")
    payload = env.get("payload")
    if not isinstance(payload, dict):
        raise BadEvent("invalid payload")
    if env["event_type"] == JOB_CREATED:
        _validate_created_payload(payload)
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
