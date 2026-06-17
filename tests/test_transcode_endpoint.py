import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routes import job as job_route


class FakeRedis:
    """Async Redis covering the transcode endpoint's read + write path."""

    def __init__(self):
        self.hashes = {}

    async def hgetall(self, k):
        return dict(self.hashes.get(k, {}))

    async def hset(self, k, mapping=None):
        self.hashes.setdefault(k, {}).update(mapping or {})
        return len(mapping or {})


@pytest.fixture
def client(monkeypatch):
    fake = FakeRedis()
    spy = []
    monkeypatch.setattr(job_route, "get_client", lambda: fake)
    monkeypatch.setattr(job_route, "publish", lambda env: spy.append(env))
    monkeypatch.setattr(job_route, "under_pressure", lambda: False)   # deterministic: not shedding by default
    return TestClient(app, raise_server_exceptions=False), fake, spy


def test_transcode_sheds_under_storage_pressure(client, monkeypatch):
    c, fake, spy = client
    seed_awaiting(fake, "jp", ["480p"])
    monkeypatch.setattr(job_route, "under_pressure", lambda: True)
    r = c.post("/jobs/jp/transcode", json={"presets": ["480p"]})
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["code"] == "STORAGE_PRESSURE" and err["retryable"] is True
    assert spy == [] and fake.hashes["job:jp"]["status"] == "awaiting_choice"   # no enqueue, state untouched


def seed_awaiting(fake, job_id, presets):
    fake.hashes[f"job:{job_id}"] = {
        "status": "awaiting_choice",
        "recommended_presets": json.dumps(presets),
        "source_meta": json.dumps({"duration": 30.0, "height": 1080}),
    }


def test_unknown_job_is_404(client):
    c, _, spy = client
    r = c.post("/jobs/j_nope/transcode", json={"presets": ["480p"]})
    assert r.status_code == 404
    assert spy == []


def test_wrong_state_is_409(client):
    c, fake, spy = client
    fake.hashes["job:j1"] = {"status": "inspecting"}
    r = c.post("/jobs/j1/transcode", json={"presets": ["480p"]})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "WRONG_STATE"
    assert spy == []   # no fact produced for a rejected commit


def test_non_recommended_preset_is_422_naming_offender(client):
    c, fake, spy = client
    seed_awaiting(fake, "j2", ["1080p", "720p", "480p"])
    r = c.post("/jobs/j2/transcode", json={"presets": ["1080p", "1440p"]})
    assert r.status_code == 422
    assert "1440p" in r.json()["error"]["message"]
    assert spy == []


def test_empty_presets_is_422(client):
    c, fake, spy = client
    seed_awaiting(fake, "j3", ["480p"])
    r = c.post("/jobs/j3/transcode", json={"presets": []})
    assert r.status_code == 422
    assert spy == []


def test_valid_commit_queues_and_produces_exactly_one_event(client):
    c, fake, spy = client
    seed_awaiting(fake, "j4", ["1080p", "720p", "480p"])
    r = c.post("/jobs/j4/transcode", json={"presets": ["720p", "480p"], "subtitles": True})
    assert r.status_code == 202
    assert r.json()["status"] == "queued"

    # state + choices persisted
    h = fake.hashes["job:j4"]
    assert h["status"] == "queued"
    assert json.loads(h["presets"]) == ["720p", "480p"]
    assert h["subtitles"] == "true"

    # exactly one job.created, keyed to this job, carrying the choices + duration
    assert len(spy) == 1
    env = spy[0]
    assert env.event_type == "job.created"
    assert env.job_id == "j4"
    assert env.payload["presets"] == ["720p", "480p"]
    assert env.payload["subtitles"] is True
    assert env.payload["source_duration"] == 30.0
