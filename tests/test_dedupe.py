import asyncio
from uuid import uuid4

import pytest
import redis
import redis.asyncio as aioredis
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routes import upload as up
from app.core.config import config
from app.storage import dedupe
from app.storage.state import ACTIVE_DEADLINES


class FakeRedis:
    def __init__(self):
        self.kv = {}
        self.hashes = {}
        self.zsets = {}
        self.expiries = []

    async def eval(self, _script, key_count, *args):
        if key_count == 4:
            content_key, job_key, stats_key, deadlines_key, job_id, _ttl, deadline, *extra = args
            owner = self.kv.get(content_key)
            status = self.hashes.get(f"job:{owner}", {}).get("status") if owner else None
            if status in {"inspecting", "awaiting_choice", "queued", "transcoding", "done"}:
                return ["hit", owner, status]
            self.kv[content_key] = job_id
            self.hashes[job_key] = {
                "status": "inspecting",
                **dict(zip(extra[::2], extra[1::2], strict=True)),
            }
            counts = self.hashes.setdefault(stats_key, {})
            counts["inspecting"] = int(counts.get("inspecting", 0)) + 1
            self.zsets.setdefault(deadlines_key, {})[job_id] = float(deadline)
            return ["miss", job_id, "inspecting"]
        if key_count == 2:
            content_key, job_key, owner = args
            if self.kv.get(content_key) == owner and self.hashes.get(job_key, {}).get("status") == "done":
                del self.kv[content_key]
                return 1
            return 0
        if key_count == 1:
            content_key, owner = args
            if self.kv.get(content_key) == owner:
                del self.kv[content_key]
                return 1
            return 0
        raise AssertionError(f"unexpected key count: {key_count}")

    async def expire(self, key, ttl):
        self.expiries.append((key, ttl))
        return True

def _resolve(r, sha="sha", job_id="new"):
    return dedupe.resolve_upload(
        r,
        sha,
        job_id,
        {
            "source_filename": "clip.mp4",
            "content_hash": sha,
            "source_path": f"/uploads/{job_id}/source.mp4",
            "created_at": "now",
        },
    )


@pytest.mark.parametrize("status", ["inspecting", "awaiting_choice", "queued", "transcoding", "done"])
def test_resolve_reuses_live_owner(status):
    r = FakeRedis()
    r.kv["content:sha"] = "old"
    r.hashes["job:old"] = {"status": status}

    result = asyncio.run(_resolve(r))

    assert result == dedupe.UploadResolution("hit", "old", status)
    assert "job:new" not in r.hashes
    assert r.hashes.get("stats:active", {}).get("inspecting", 0) == 0


@pytest.mark.parametrize("status", [None, "failed", "cancelled", "expired", "unknown"])
def test_resolve_replaces_terminal_or_missing_owner(status):
    r = FakeRedis()
    r.kv["content:sha"] = "old"
    if status:
        r.hashes["job:old"] = {"status": status}

    result = asyncio.run(_resolve(r))

    assert result == dedupe.UploadResolution("miss", "new", "inspecting")
    assert r.kv["content:sha"] == "new"
    assert r.hashes["job:new"]["content_hash"] == "sha"
    assert r.hashes["stats:active"]["inspecting"] == 1


def test_parallel_resolution_has_one_winner():
    client = redis.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
    try:
        client.ping()
    except redis.RedisError:
        pytest.skip("redis not reachable on 127.0.0.1:6379")
    finally:
        client.close()

    async def run():
        r = aioredis.Redis(host="127.0.0.1", port=6379, db=15, decode_responses=True)
        sha = f"it_upload_{uuid4().hex}"
        first, second = f"j_{uuid4().hex}", f"j_{uuid4().hex}"
        content_key = f"content:{sha}"
        before = int(await r.hget("stats:active", "inspecting") or 0)
        try:
            results = await asyncio.gather(
                _resolve(r, sha, first),
                _resolve(r, sha, second),
            )
            owner = await r.get(content_key)
            after = int(await r.hget("stats:active", "inspecting") or 0)
            return results, owner, after - before
        finally:
            await r.delete(content_key, f"job:{first}", f"job:{second}")
            await r.zrem(ACTIVE_DEADLINES, first, second)
            await r.hset("stats:active", "inspecting", before)
            await r.aclose()

    results, owner, count_delta = asyncio.run(run())

    assert sorted(result.outcome for result in results) == ["hit", "miss"]
    assert all(result.job_id == owner for result in results)
    assert count_delta == 1


def test_new_upload_registers_an_active_deadline(monkeypatch):
    class RecordingRedis:
        def __init__(self):
            self.call = None

        async def eval(self, _script, key_count, *args):
            self.call = (key_count, args)
            return ["miss", "new", "inspecting"]

    r = RecordingRedis()
    monkeypatch.setattr(dedupe.time, "time", lambda: 1000.0)

    asyncio.run(_resolve(r))

    key_count, args = r.call
    assert key_count == 4
    assert args[3] == ACTIVE_DEADLINES
    assert float(args[6]) == 1000 + config.output_ttl_days * 86400


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "data_dir", tmp_path)
    monkeypatch.setattr(up, "under_pressure", lambda: False)
    fake = FakeRedis()
    monkeypatch.setattr(up, "get_client", lambda: fake)
    sent = []
    monkeypatch.setattr(
        up.celery_app,
        "send_task",
        lambda name, args=None: sent.append((name, args)),
    )
    return TestClient(app, raise_server_exceptions=False), fake, sent


def test_duplicate_upload_hits_owner_without_counter_drift(client):
    http, fake, sent = client
    body = b"dupe-me"

    first = http.post("/upload?filename=a.mp4", content=body).json()
    second = http.post("/upload?filename=b.mp4", content=body).json()

    assert first["dedupe"] == "miss"
    assert second == {
        "job_id": first["job_id"],
        "status": "inspecting",
        "dedupe": "hit",
    }
    assert len(sent) == 1
    assert fake.hashes["stats:active"]["inspecting"] == 1


@pytest.mark.parametrize("status", ["failed", "cancelled", "expired"])
def test_terminal_owner_runs_fresh(client, status):
    http, fake, sent = client
    body = f"owner-{status}".encode()
    first = http.post("/upload?filename=a.mp4", content=body).json()
    fake.hashes[f"job:{first['job_id']}"]["status"] = status

    second = http.post("/upload?filename=b.mp4", content=body).json()

    assert second["dedupe"] == "miss"
    assert second["job_id"] != first["job_id"]
    assert len(sent) == 2


def test_done_owner_with_missing_manifest_runs_fresh(client):
    http, fake, sent = client
    body = b"missing-artifacts"
    first = http.post("/upload?filename=a.mp4", content=body).json()
    fake.hashes[f"job:{first['job_id']}"]["status"] = "done"

    second = http.post("/upload?filename=b.mp4", content=body).json()

    assert second["dedupe"] == "miss"
    assert second["job_id"] != first["job_id"]
    assert len(sent) == 2


def test_done_owner_with_manifest_is_reused_and_refreshes_ttl(client, tmp_path):
    http, fake, sent = client
    body = b"complete-artifacts"
    first = http.post("/upload?filename=a.mp4", content=body).json()
    fake.hashes[f"job:{first['job_id']}"]["status"] = "done"
    job_dir = tmp_path / "output" / first["job_id"]
    job_dir.mkdir(parents=True)
    (job_dir / "manifest.json").write_text("{}")

    second = http.post("/upload?filename=b.mp4", content=body).json()

    assert second == {"job_id": first["job_id"], "status": "done", "dedupe": "hit"}
    assert fake.expiries == [(f"content:{next(iter(fake.kv)).split(':', 1)[1]}", config.output_ttl_days * 86400)]
    assert len(sent) == 1
