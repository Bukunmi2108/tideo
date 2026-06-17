import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routes import upload as up
from app.core.config import config
from app.storage import dedupe


class FakeRedis:
    """Minimal async Redis with real SET NX semantics — enough for the dedupe paths."""

    def __init__(self):
        self.kv = {}
        self.hashes = {}

    async def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    async def get(self, k):
        return self.kv.get(k)

    async def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)
            self.hashes.pop(k, None)

    async def hset(self, k, mapping=None):
        self.hashes.setdefault(k, {}).update(mapping or {})
        return len(mapping or {})

    async def hincrby(self, k, f, n):
        return None

    async def hgetall(self, k):
        return dict(self.hashes.get(k, {}))

    async def hget(self, k, field):
        return self.hashes.get(k, {}).get(field)


# ---------- dedupe.py units ----------

def test_claim_is_atomic_first_wins():
    r = FakeRedis()

    async def go():
        a = await dedupe.claim(r, "sha", "jobA")
        b = await dedupe.claim(r, "sha", "jobB")
        return a, b, await dedupe.owner(r, "sha")

    a, b, owner = asyncio.run(go())
    assert a is True and b is False and owner == "jobA"


def test_is_valid_record_present_not_failed():
    r = FakeRedis()

    async def go():
        await r.hset("job:ok", mapping={"status": "inspecting"})
        await r.hset("job:bad", mapping={"status": "failed"})
        return (
            await dedupe.is_valid(r, "ok"),
            await dedupe.is_valid(r, "bad"),
            await dedupe.is_valid(r, "missing"),
        )

    ok, bad, missing = asyncio.run(go())
    assert ok is True and bad is False and missing is False


def test_reclaim_overwrites_owner():
    r = FakeRedis()

    async def go():
        await dedupe.claim(r, "sha", "old")
        await dedupe.reclaim(r, "sha", "new")
        return await dedupe.owner(r, "sha")

    assert asyncio.run(go()) == "new"


def test_parallel_claims_yield_one_winner():
    r = FakeRedis()

    async def go():
        return await asyncio.gather(dedupe.claim(r, "s", "A"), dedupe.claim(r, "s", "B"))

    assert sum(1 for x in asyncio.run(go()) if x) == 1


# ---------- route integration ----------

@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "data_dir", tmp_path)
    fake = FakeRedis()
    monkeypatch.setattr(up, "get_client", lambda: fake)
    send = MagicMock()
    monkeypatch.setattr(up.celery_app, "send_task", send)
    return TestClient(app, raise_server_exceptions=False), fake, send


def test_duplicate_upload_hits_existing_job(client):
    c, _, send = client
    body = b"dupe-me"
    r1 = c.post("/upload?filename=a.mp4", content=body).json()
    r2 = c.post("/upload?filename=b.mp4", content=body).json()
    assert r1["dedupe"] == "miss"
    assert r2["dedupe"] == "hit"
    assert r2["job_id"] == r1["job_id"]   # attached to the original
    assert send.call_count == 1           # no second inspect task


def test_failed_owner_runs_fresh(client):
    c, fake, send = client
    body = b"failed-one"
    r1 = c.post("/upload?filename=a.mp4", content=body).json()
    fake.hashes[f"job:{r1['job_id']}"]["status"] = "failed"
    r2 = c.post("/upload?filename=b.mp4", content=body).json()
    assert r2["dedupe"] == "miss" and r2["job_id"] != r1["job_id"]
    assert send.call_count == 2


def test_stale_owner_runs_fresh(client):
    c, fake, send = client
    body = b"stale-one"
    r1 = c.post("/upload?filename=a.mp4", content=body).json()
    fake.hashes.pop(f"job:{r1['job_id']}", None)   # owner record wiped
    r2 = c.post("/upload?filename=b.mp4", content=body).json()
    assert r2["dedupe"] == "miss"
    assert send.call_count == 2
