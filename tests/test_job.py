import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routes import job as job_route
from app.api.session import hash_session_token
from app.storage.job_control import CancelResult

SESSION_TOKEN = "v1." + "A" * 43
OTHER_TOKEN = "v1." + "B" * 43
SESSION_HASH = hash_session_token(SESSION_TOKEN)
SESSION_HEADERS = {"X-Tideo-Session": SESSION_TOKEN}


class FakeRedis:
    """Async Redis with the read path GET /jobs/{id} needs, plus cancel's writes."""

    def __init__(self):
        self.hashes = {}
        self.kv = {}
        self.zsets = {}

    async def hgetall(self, k):
        return dict(self.hashes.get(k, {}))

    async def set(self, k, v, ex=None):
        self.kv[k] = v

    async def hget(self, k, f):
        return self.hashes.get(k, {}).get(f)

    async def hset(self, k, mapping=None):
        self.hashes.setdefault(k, {}).update({kk: str(vv) for kk, vv in (mapping or {}).items()})

    async def hincrby(self, k, f, n):
        return None

    async def publish(self, ch, msg):
        pass

    async def expire(self, k, ttl):
        return True

    async def zrevrange(self, key, start, end):
        members = sorted(
            self.zsets.get(key, {}).items(),
            key=lambda item: item[1],
            reverse=True,
        )
        stop = None if end == -1 else end + 1
        return [member for member, _score in members[start:stop]]

    async def zrem(self, key, *members):
        for member in members:
            self.zsets.get(key, {}).pop(member, None)


@pytest.fixture
def client(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(job_route, "get_client", lambda: fake)
    monkeypatch.setattr(job_route, "db_get_job", lambda jid: None)   # default: no cold row (override per test)
    monkeypatch.setattr(job_route, "db_list_jobs", lambda **k: [])   # so no test hits a real Postgres

    async def cancel_job(r, job_id, *, ttl):
        rec = r.hashes.get(f"job:{job_id}")
        if not rec:
            return CancelResult("missing", None)
        status = rec["status"]
        if status not in ("queued", "transcoding"):
            return CancelResult("wrong_state", status)
        rec["status"] = "cancelled"
        r.kv[f"cancel:{job_id}"] = "1"
        ids = json.loads(rec.get("rendition_ids") or "[]")
        ids.extend(v for v in (rec.get("chord_callback_id"), rec.get("transcribe_id")) if v)
        return CancelResult("cancelled", status, tuple(ids))

    monkeypatch.setattr(job_route, "acancel_job", cancel_job)
    return TestClient(
        app,
        raise_server_exceptions=False,
        headers=SESSION_HEADERS,
    ), fake


def seed(fake, job_id, **fields):
    fields.setdefault("owner_session_hash", SESSION_HASH)
    fake.hashes[f"job:{job_id}"] = {k: str(v) for k, v in fields.items()}


def source_meta(**overrides):
    meta = {
        "container": "mp4",
        "video_codec": "h264",
        "audio_codec": "aac",
        "width": 1920,
        "height": 1080,
        "duration": 60.0,
        "bitrate": 5_000_000,
        "fps": 30.0,
        "has_audio": True,
        "video_streams": 1,
        "audio_streams": 1,
    }
    return {**meta, **overrides}


# ---- 404 / 410 contracts ----

def test_unknown_id_is_404(client):
    c, _ = client
    r = c.get("/jobs/j_nope")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_foreign_and_ownerless_jobs_are_indistinguishable_from_unknown(client):
    c, fake = client
    seed(fake, "foreign", status="inspecting")
    seed(fake, "legacy", status="done", owner_session_hash="")

    foreign = c.get(
        "/jobs/foreign",
        headers={"X-Tideo-Session": OTHER_TOKEN},
    )
    legacy = c.get("/jobs/legacy")

    assert foreign.status_code == legacy.status_code == 404
    assert foreign.json()["error"]["code"] == "JOB_NOT_FOUND"
    assert legacy.json()["error"]["code"] == "JOB_NOT_FOUND"


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
    meta = source_meta()
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
        source_meta=json.dumps(source_meta(height=720)),
        recommended_presets=json.dumps(["720p"]),
        web_safe="false",
        web_safe_reason="container is matroska",
    )
    body = c.get("/jobs/j2b").json()
    assert body["web_safe"] is False
    assert body["web_safe_reason"] == "container is matroska"


def test_awaiting_choice_malformed_recommendations_degrade(client):
    c, fake = client
    seed(
        fake,
        "j2c",
        status="awaiting_choice",
        source_meta=json.dumps(source_meta()),
        recommended_presets=json.dumps({"720p": True}),
        web_safe="true",
    )

    response = c.get("/jobs/j2c")

    assert response.status_code == 200
    assert response.json()["recommended_presets"] == []


@pytest.mark.parametrize("status", ["queued", "transcoding"])
def test_in_progress_returns_progress_and_presets(client, status):
    c, fake = client
    seed(fake, "j3", status=status, presets=json.dumps(["720p", "480p"]))
    fake.hashes["job:j3"]["progress:720p"] = "100.0"
    fake.hashes["job:j3"]["progress:480p"] = "42.5"
    body = c.get("/jobs/j3").json()
    assert body["status"] == status
    assert body["progress"] == {"720p": 100.0, "480p": 42.5}
    assert body["presets"] == ["720p", "480p"]  # the bar set, known before any progress


def test_in_progress_with_no_renditions_yet_is_empty_map(client):
    c, fake = client
    seed(fake, "j3b", status="queued")
    body = c.get("/jobs/j3b").json()
    assert body["progress"] == {}
    assert body["presets"] == []


def test_in_progress_malformed_fields_degrade(client):
    c, fake = client
    seed(fake, "j3c", status="transcoding", presets="{truncated")
    fake.hashes["job:j3c"]["progress:720p"] = "not-a-number"

    response = c.get("/jobs/j3c")

    assert response.status_code == 200
    assert response.json()["presets"] == []
    assert response.json()["progress"] == {}


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
        "subtitles": None,
    }


def test_done_returns_exact_output_expiry(client, monkeypatch):
    c, fake = client
    seed(
        fake,
        "j4-expiry",
        status="done",
        presets=json.dumps(["720p"]),
        source_meta=json.dumps({"duration": 60}),
        finished_at="2026-06-17T11:01:00+00:00",
    )
    monkeypatch.setattr(job_route.config, "output_ttl_days", 7)

    body = c.get("/jobs/j4-expiry").json()

    assert body["expires_at"] == "2026-06-24T11:01:00+00:00"


# ---- cancel ----

@pytest.mark.parametrize("status", ["queued", "transcoding"])
def test_cancel_transitions_flags_and_revokes(client, monkeypatch, tmp_path, status):
    c, fake = client
    seed(
        fake,
        "jc",
        status=status,
        rendition_ids=json.dumps(["r0", "r1"]),
        chord_callback_id="cb",
        transcribe_id="stt",
    )
    revoked, published = [], []
    monkeypatch.setattr(job_route.celery_app.control, "revoke", lambda ids, **k: revoked.append((ids, k)))
    persisted = []
    monkeypatch.setattr(job_route, "emit", lambda event_type, *_a, **_k: published.append(event_type))

    async def persist(_r, job_id):
        persisted.append(job_id)
        return True

    monkeypatch.setattr(job_route.terminal_outbox, "adrain_one", persist)
    monkeypatch.setattr(job_route, "_remove_cancelled_outputs", lambda jid: None)

    r = c.post("/jobs/jc/cancel")

    assert r.status_code == 202 and r.json()["status"] == "cancelled"
    assert persisted == ["jc"]                               # durable cancelled row written
    assert fake.hashes["job:jc"]["status"] == "cancelled"
    assert fake.kv["cancel:jc"] == "1"                       # flag the running encode loop
    assert revoked[0][0] == ["r0", "r1", "cb", "stt"]
    assert "job.cancelled" in published


@pytest.mark.parametrize("status", ["done", "failed", "awaiting_choice"])
def test_cancel_wrong_state_is_409(client, status):
    c, fake = client
    seed(fake, "jx", status=status)
    assert c.post("/jobs/jx/cancel").status_code == 409


def test_cancel_unknown_is_404(client):
    c, _ = client
    assert c.post("/jobs/nope/cancel").status_code == 404


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


def test_retryable_failure_is_exposed(client):
    c, fake = client
    seed(
        fake,
        "j5b",
        status="failed",
        error_code="ENCODE_TIMEOUT",
        error_message="timed out",
        error_stage="transcode",
    )

    error = c.get("/jobs/j5b").json()["error"]

    assert error["retryable"] is True


# ---- GET /jobs (history list, from Postgres) ----

from datetime import UTC, datetime


def _pg_row(job_id, status="done", **kw):
    row = {
        "job_id": job_id, "status": status, "content_hash": "h", "source_filename": f"{job_id}.mp4",
        "source_duration_s": 60.0, "presets": ["720p", "480p"],
        "error_code": None, "error_message": None, "error_stage": None,
        "created_at": datetime(2026, 6, 17, 10, 0, tzinfo=UTC),
        "started_at": datetime(2026, 6, 17, 10, 0, 5, tzinfo=UTC),
        "finished_at": datetime(2026, 6, 17, 10, 1, tzinfo=UTC),
        "expired_at": None,
        "owner_session_hash": SESSION_HASH,
    }
    row.update(kw)
    return row


def test_list_paginates_and_reports_has_more(client, monkeypatch):
    c, _ = client
    # db returns limit+1 rows -> has_more True, and the extra row is trimmed from items
    rows = [_pg_row(f"j{i}") for i in range(3)]
    monkeypatch.setattr(job_route, "db_list_jobs", lambda **k: rows)
    r = c.get("/jobs?limit=2")
    assert r.status_code == 200
    body = r.json()
    assert body["has_more"] is True and body["limit"] == 2 and body["offset"] == 0
    assert [it["job_id"] for it in body["items"]] == ["j2", "j1"]
    assert body["items"][0]["source_filename"] == "j2.mp4" and body["items"][0]["duration"] == 60.0


def test_list_no_more_when_under_limit(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(job_route, "db_list_jobs", lambda **k: [_pg_row("j0")])
    body = c.get("/jobs?limit=20").json()
    assert body["has_more"] is False and len(body["items"]) == 1


def test_list_merges_active_redis_jobs_with_terminal_history(client, monkeypatch):
    c, fake = client
    seed(
        fake,
        "active",
        status="transcoding",
        source_filename="active.mp4",
        source_meta=json.dumps({"duration": 30.0}),
        created_at="2026-06-17T11:00:00+00:00",
    )
    fake.zsets[f"session:{SESSION_HASH}:jobs"] = {"active": 2.0}
    monkeypatch.setattr(
        job_route,
        "db_list_jobs",
        lambda **_kwargs: [_pg_row("terminal", created_at=datetime(2026, 6, 17, 10, 0, tzinfo=UTC))],
    )

    body = c.get("/jobs").json()

    assert [item["job_id"] for item in body["items"]] == ["active", "terminal"]
    assert body["items"][0]["status"] == "transcoding"


def test_list_accepts_active_status_filter(client):
    c, fake = client
    seed(
        fake,
        "active",
        status="inspecting",
        source_filename="active.mp4",
        created_at="2026-06-17T11:00:00+00:00",
    )
    fake.zsets[f"session:{SESSION_HASH}:jobs"] = {"active": 2.0}

    response = c.get("/jobs?status=inspecting")

    assert response.status_code == 200
    assert [item["job_id"] for item in response.json()["items"]] == ["active"]


def test_list_processing_group_includes_active_progress(client, monkeypatch):
    c, fake = client
    seed(
        fake,
        "active",
        status="transcoding",
        source_filename="active.mp4",
        presets=json.dumps(["720p", "480p"]),
        **{"progress:720p": "50", "progress:480p": "25"},
    )
    fake.zsets[f"session:{SESSION_HASH}:jobs"] = {"active": 2.0}
    monkeypatch.setattr(job_route, "db_list_jobs", lambda **_kwargs: [])

    item = c.get("/jobs?status=processing").json()["items"][0]

    assert item["status"] == "transcoding"
    assert item["presets"] == ["720p", "480p"]
    assert item["progress"] == {"720p": 50.0, "480p": 25.0}


def test_list_ready_group_maps_to_done(client, monkeypatch):
    c, _ = client
    captured = {}

    def spy(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(job_route, "db_list_jobs", spy)
    assert c.get("/jobs?status=ready").status_code == 200
    assert captured["status"] == "done"


def test_list_forwards_status_limit_offset_to_db(client, monkeypatch):
    c, _ = client
    captured = {}

    def spy(*, owner_session_hash, status=None, limit=20, offset=0):
        captured.update(
            owner_session_hash=owner_session_hash,
            status=status,
            limit=limit,
            offset=offset,
        )
        return []

    monkeypatch.setattr(job_route, "db_list_jobs", spy)
    c.get("/jobs?status=done&limit=5&offset=10")
    assert captured == {
        "owner_session_hash": SESSION_HASH,
        "status": "done",
        "limit": 16,
        "offset": 0,
    }


@pytest.mark.parametrize("qs,code", [
    ("limit=0", 422), ("limit=51", 422), ("offset=-1", 422), ("limit=50", 200), ("limit=1", 200),
])
def test_list_enforces_limit_offset_bounds(client, qs, code):
    c, _ = client
    assert c.get(f"/jobs?{qs}").status_code == code


def test_list_handles_null_duration(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(job_route, "db_list_jobs",
                        lambda **k: [_pg_row("j0", status="failed", source_duration_s=None)])
    assert c.get("/jobs").json()["items"][0]["duration"] is None


def test_list_rejects_unknown_status_filter(client):
    c, _ = client
    assert c.get("/jobs?status=banana").status_code == 422


def test_list_computes_expires_at_and_poster_for_done(client, monkeypatch, tmp_path):
    c, _ = client
    out = tmp_path / "output" / "j0"      # config.output_dir is data_dir/"output" (a property)
    out.mkdir(parents=True)
    (out / "poster.jpg").write_bytes(b"x")
    monkeypatch.setattr(job_route.config, "data_dir", tmp_path)
    monkeypatch.setattr(job_route.config, "output_ttl_days", 7)
    monkeypatch.setattr(job_route, "db_list_jobs", lambda **k: [_pg_row("j0", status="done")])
    item = c.get("/jobs").json()["items"][0]
    assert item["poster"] == "/jobs/j0/poster"
    assert item["expires_at"].startswith("2026-06-24")          # finished 06-17 + 7d


def test_list_failed_job_has_no_poster_or_expiry(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(job_route, "db_list_jobs", lambda **k: [_pg_row("j0", status="failed")])
    item = c.get("/jobs").json()["items"][0]
    assert item["poster"] is None and item["expires_at"] is None


# ---- GET /jobs/{id} cold-tier fallback (Redis hash gone) ----

def test_get_falls_back_to_postgres_for_done(client, monkeypatch):
    c, _ = client                                                # fake redis empty -> miss -> PG
    monkeypatch.setattr(job_route, "db_get_job", lambda jid: _pg_row(jid, status="done"))
    body = c.get("/jobs/jgone").json()
    assert body["status"] == "done"
    assert body["results"]["playlist"] == "/jobs/jgone/playlist"
    assert body["results"]["presets"] == ["720p", "480p"] and body["results"]["duration"] == 60.0
    assert body["expires_at"].startswith("2026-06-24")


def test_get_falls_back_to_postgres_for_failed(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(job_route, "db_get_job",
                        lambda jid: _pg_row(jid, status="failed", error_code="ENCODE_TIMEOUT",
                                            error_stage="transcode"))
    body = c.get("/jobs/jgone").json()
    assert body["status"] == "failed" and body["error"]["code"] == "ENCODE_TIMEOUT"
    assert body["error"]["retryable"] is True


def test_get_expired_cold_row_is_410(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(job_route, "db_get_job", lambda jid: _pg_row(jid, status="expired"))
    assert c.get("/jobs/jgone").status_code == 410


def test_get_falls_back_cancelled_is_minimal_shape(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(job_route, "db_get_job", lambda jid: _pg_row(jid, status="cancelled"))
    body = c.get("/jobs/jgone").json()
    assert body["status"] == "cancelled" and body["results"] is None and body["error"] is None


def test_get_unknown_in_both_tiers_is_404(client, monkeypatch):
    c, _ = client
    monkeypatch.setattr(job_route, "db_get_job", lambda jid: None)
    assert c.get("/jobs/jgone").status_code == 404


def test_get_db_outage_on_read_is_503(client, monkeypatch):
    import psycopg2
    c, _ = client

    def boom(jid):
        raise psycopg2.OperationalError("postgres down")

    monkeypatch.setattr(job_route, "db_get_job", boom)
    assert c.get("/jobs/jgone").status_code == 503   # transient outage -> retryable 503, not a lying 404/500
