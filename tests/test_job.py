import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.model import progress_map, results_view
from app.api.routes import job as job_route


class FakeRedis:
    """Async Redis with just the read path GET /jobs/{id} needs."""

    def __init__(self):
        self.hashes = {}

    async def hgetall(self, k):
        return dict(self.hashes.get(k, {}))


@pytest.fixture
def client(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(job_route, "get_client", lambda: fake)
    return TestClient(app, raise_server_exceptions=False), fake


def seed(fake, job_id, **fields):
    fake.hashes[f"job:{job_id}"] = {k: str(v) for k, v in fields.items()}


# ---- 404 / 410 contracts ----

def test_unknown_id_is_404(client):
    c, _ = client
    r = c.get("/jobs/j_nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_expired_is_410(client):
    c, fake = client
    seed(fake, "j_old", status="expired")
    r = c.get("/jobs/j_old")
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "JOB_EXPIRED"


# ---- per-state response shapes ----

def test_inspecting_returns_status_only(client):
    c, fake = client
    seed(fake, "j1", status="inspecting")
    body = c.get("/jobs/j1").json()
    assert body["status"] == "inspecting"
    assert body["source"] is None and body["error"] is None


def test_awaiting_choice_shape(client):
    c, fake = client
    meta = {"container": "mp4", "video_codec": "h264", "height": 1080}
    seed(
        fake, "j2",
        status="awaiting_choice",
        source_meta=json.dumps(meta),
        source_filename="holiday.mov",
        recommended_presets=json.dumps(["1080p", "720p", "480p", "360p"]),
        web_safe="true",
        web_safe_reason="",
    )
    body = c.get("/jobs/j2").json()
    assert body["status"] == "awaiting_choice"
    assert body["source"] == meta
    assert body["source_filename"] == "holiday.mov"
    assert body["recommended_presets"] == ["1080p", "720p", "480p", "360p"]
    assert body["web_safe"] is True
    assert body["web_safe_reason"] is None  # empty stored reason -> None


def test_web_safe_false_keeps_reason(client):
    c, fake = client
    seed(
        fake, "j2b",
        status="awaiting_choice",
        source_meta=json.dumps({"height": 720}),
        recommended_presets=json.dumps(["720p"]),
        web_safe="false",
        web_safe_reason="container is matroska",
    )
    body = c.get("/jobs/j2b").json()
    assert body["web_safe"] is False
    assert body["web_safe_reason"] == "container is matroska"


@pytest.mark.parametrize("status", ["queued", "transcoding"])
def test_in_progress_returns_progress_map(client, status):
    c, fake = client
    seed(fake, "j3", status=status)
    fake.hashes["job:j3"]["progress:720p"] = "100.0"
    fake.hashes["job:j3"]["progress:480p"] = "42.5"
    body = c.get("/jobs/j3").json()
    assert body["status"] == status
    assert body["progress"] == {"720p": 100.0, "480p": 42.5}


def test_in_progress_with_no_renditions_yet_is_empty_map(client):
    c, fake = client
    seed(fake, "j3b", status="queued")
    body = c.get("/jobs/j3b").json()
    assert body["progress"] == {}


def test_done_returns_results_urls(client):
    c, fake = client
    seed(
        fake, "j4",
        status="done",
        presets=json.dumps(["720p", "480p"]),
        source_meta=json.dumps({"duration": 120.5}),
    )
    body = c.get("/jobs/j4").json()
    assert body["status"] == "done"
    assert body["results"] == {
        "playlist": "/jobs/j4/playlist",
        "web_mp4": "/jobs/j4/file",
        "poster": "/jobs/j4/poster",
        "sprite": "/jobs/j4/sprite",
        "player": "/jobs/j4/player",
        "presets": ["720p", "480p"],
        "duration": 120.5,
    }


# ---- results_view / progress_map: pure helpers, shared by GET + WS ----

def test_results_view_full():
    rec = {"presets": json.dumps(["720p", "480p"]), "source_meta": json.dumps({"duration": 60.0})}
    out = results_view("jX", rec)
    assert out["playlist"] == "/jobs/jX/playlist"
    assert out["presets"] == ["720p", "480p"]
    assert out["duration"] == 60.0


def test_results_view_missing_fields_degrades():
    out = results_view("jX", {})
    assert out["presets"] == []
    assert out["duration"] is None
    assert out["playlist"] == "/jobs/jX/playlist"


def test_results_view_source_meta_without_duration():
    out = results_view("jX", {"source_meta": json.dumps({"width": 1280})})
    assert out["duration"] is None


@pytest.mark.parametrize("bad", ["{truncated", "not json", "null"])
def test_results_view_malformed_json_fails_soft(bad):
    out = results_view("jX", {"presets": bad, "source_meta": bad})
    assert out["presets"] == []
    assert out["duration"] is None
    assert out["web_mp4"] == "/jobs/jX/file"


def test_progress_map_extracts_only_progress_fields():
    rec = {"status": "transcoding", "progress:720p": "100.0", "progress:480p": "0.0", "source_path": "/x"}
    assert progress_map(rec) == {"720p": 100.0, "480p": 0.0}


def test_failed_returns_error_envelope(client):
    c, fake = client
    seed(
        fake, "j5",
        status="failed",
        error_code="SOURCE_NO_VIDEO",
        error_message="no video stream",
        error_stage="inspect",
    )
    body = c.get("/jobs/j5")
    assert body.status_code == 200  # the job exists; the GET succeeds
    err = body.json()["error"]
    assert err["code"] == "SOURCE_NO_VIDEO"
    assert err["stage"] == "inspect"
    assert err["retryable"] is False
