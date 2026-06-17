import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routes import upload as up
from app.core.config import config


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "data_dir", tmp_path)              # uploads_dir -> tmp_path/uploads
    fake_redis = AsyncMock()
    monkeypatch.setattr(up, "get_client", lambda: fake_redis)
    monkeypatch.setattr(up.celery_app, "send_task", MagicMock())   # don't touch the broker
    monkeypatch.setattr(up, "under_pressure", lambda: False)       # deterministic: not shedding by default
    return TestClient(app, raise_server_exceptions=False), fake_redis, tmp_path


def test_upload_sheds_under_storage_pressure(client, monkeypatch):
    c, fake_redis, _ = client
    monkeypatch.setattr(up, "under_pressure", lambda: True)
    r = c.post("/upload?filename=clip.mp4", content=b"hello")
    assert r.status_code == 503
    err = r.json()["error"]
    assert err["code"] == "STORAGE_PRESSURE" and err["retryable"] is True
    fake_redis.hset.assert_not_called()                            # shed before any work (no job created)


def test_valid_upload_returns_202_and_hashes(client):
    c, fake_redis, tmp_path = client
    body = b"hello world"
    r = c.post("/upload?filename=clip.mp4", content=body)
    assert r.status_code == 202
    j = r.json()
    assert j["status"] == "inspecting" and j["dedupe"] == "miss"
    assert fake_redis.hset.call_args.kwargs["mapping"]["content_hash"] == hashlib.sha256(body).hexdigest()
    assert (tmp_path / "uploads" / j["job_id"] / "source.mp4").read_bytes() == body


def test_missing_filename_is_422(client):
    c, *_ = client
    r = c.post("/upload", content=b"x")
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_UPLOAD"


def test_bad_extension_is_415(client):
    c, *_ = client
    r = c.post("/upload?filename=x.txt", content=b"x")
    assert r.status_code == 415 and r.json()["error"]["code"] == "UNSUPPORTED_MEDIA"


def test_empty_body_is_422_and_leaves_no_dir(client):
    c, _, tmp_path = client
    r = c.post("/upload?filename=e.mp4", content=b"")
    assert r.status_code == 422 and r.json()["error"]["code"] == "INVALID_UPLOAD"
    up_dir = tmp_path / "uploads"
    assert not up_dir.exists() or not any(up_dir.iterdir())   # no orphan {job_id} dir


def test_over_limit_is_413_and_leaves_no_partial(client, monkeypatch):
    c, _, tmp_path = client
    monkeypatch.setattr(config, "max_upload_bytes", 3)
    r = c.post("/upload?filename=big.mp4", content=b"hello")   # 5 > 3
    assert r.status_code == 413 and r.json()["error"]["code"] == "UPLOAD_TOO_LARGE"
    up_dir = tmp_path / "uploads"
    assert not up_dir.exists() or not any(up_dir.rglob("*"))   # no partial / orphan


def test_unhandled_exception_returns_opaque_500(client, monkeypatch):
    c, *_ = client
    monkeypatch.setattr(up, "stream_to_disk", AsyncMock(side_effect=Exception("boom-secret")))
    r = c.post("/upload?filename=clip.mp4", content=b"hello")
    assert r.status_code == 500
    assert r.json()["error"]["code"] == "INTERNAL"
    assert "boom-secret" not in r.text   # no internals leaked
