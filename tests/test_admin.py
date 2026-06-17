import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routes import admin
from app.workers.dlq import DLQ_KEY

HDR = {"X-Admin-Token": "test-token"}  # conftest sets ADMIN_TOKEN=test-token


class FakeRedis:
    def __init__(self, records: dict):
        self.store = dict(records)

    async def hgetall(self, key):
        return dict(self.store) if key == DLQ_KEY else {}

    async def hget(self, key, field):
        return self.store.get(field)

    async def hdel(self, key, field):
        return 1 if self.store.pop(field, None) is not None else 0


def _rec(rid, task="app.workers.tasks.rendition.rendition", args=None, code="STORAGE_FULL", at="2026-06-17T01:00:00Z"):
    return json.dumps({"id": rid, "task": task, "args": args or [], "error_code": code, "failed_at": at})


@pytest.fixture
def client(monkeypatch):
    fake = FakeRedis({
        "r1": _rec("r1", args=["j1", "720p"], at="2026-06-17T01:00:00Z"),
        "r2": _rec("r2", task="other", code="ENCODE_FAILED_TRANSIENT", at="2026-06-17T02:00:00Z"),
    })
    sent = []
    monkeypatch.setattr(admin, "get_client", lambda: fake)
    monkeypatch.setattr(admin.celery_app, "send_task", lambda name, args=None: sent.append((name, args)))
    return TestClient(app, raise_server_exceptions=False), fake, sent


# ---- auth ----

@pytest.mark.parametrize("headers", [{}, {"X-Admin-Token": "wrong"}])
def test_endpoints_401_without_valid_token(client, headers):
    c, _, _ = client
    assert c.get("/admin/dlq", headers=headers).status_code == 401
    assert c.post("/admin/dlq/r1/requeue", headers=headers).status_code == 401
    assert c.delete("/admin/dlq/r1", headers=headers).status_code == 401


# ---- list (peek) ----

def test_list_returns_records_newest_first(client):
    c, _, _ = client
    body = c.get("/admin/dlq", headers=HDR).json()
    assert [r["id"] for r in body["records"]] == ["r2", "r1"]  # sorted by failed_at desc


def test_list_does_not_consume(client):
    c, fake, _ = client
    c.get("/admin/dlq", headers=HDR)
    assert set(fake.store) == {"r1", "r2"}  # browsing leaves the store intact


# ---- requeue ----

def test_requeue_resends_task_and_clears_record(client):
    c, fake, sent = client
    r = c.post("/admin/dlq/r1/requeue", headers=HDR)
    assert r.status_code == 202
    assert sent == [("app.workers.tasks.rendition.rendition", ["j1", "720p"])]
    assert "r1" not in fake.store


def test_requeue_unknown_is_404(client):
    c, _, sent = client
    assert c.post("/admin/dlq/nope/requeue", headers=HDR).status_code == 404
    assert sent == []


# ---- discard ----

def test_discard_removes_record(client):
    c, fake, _ = client
    assert c.delete("/admin/dlq/r2", headers=HDR).status_code == 200
    assert "r2" not in fake.store


def test_discard_unknown_is_404(client):
    c, _, _ = client
    assert c.delete("/admin/dlq/nope", headers=HDR).status_code == 404
