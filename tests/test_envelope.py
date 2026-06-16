import dataclasses
import json

import pytest

from app.events.envelope import SCHEMA_VERSION, Envelope


def test_event_id_is_unique_per_construction():
    a = Envelope("job.created", "j1", {})
    b = Envelope("job.created", "j1", {})
    assert a.event_id != b.event_id   # minted fresh each time -> usable as idempotency key


def test_to_json_round_trips_every_field():
    env = Envelope("rendition.completed", "j2", {"preset": "720p", "output_bytes": 42})
    d = json.loads(env.to_json())
    assert d["event_type"] == "rendition.completed"
    assert d["job_id"] == "j2"
    assert d["payload"] == {"preset": "720p", "output_bytes": 42}
    assert d["schema_version"] == SCHEMA_VERSION
    assert d["event_id"] == env.event_id
    assert set(d) == {
        "event_type", "job_id", "payload", "schema_version",
        "producer", "event_id", "timestamp",
    }


def test_envelope_is_frozen():
    env = Envelope("job.created", "j3", {})
    with pytest.raises(dataclasses.FrozenInstanceError):
        env.event_id = "tampered"  # type: ignore[misc]  # the idempotency key must be immutable


def test_schema_version_policy_is_documented():
    # Contract guard: within a schema_version, payloads are ADDITIVE ONLY (consumers
    # ignore unknown fields). A rename/repurpose is breaking and MUST bump this number.
    # If this assertion ever changes, every consumer's dual-version handling must too.
    assert SCHEMA_VERSION == 1
