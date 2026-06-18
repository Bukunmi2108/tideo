import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routes import status as st
from app.storage import state as stmod


# ---------- write_status: active-state counter maintenance ----------

class FakeRedis:
    def __init__(self):
        self.hashes, self.counts = {}, {}

    def hget(self, k, f):
        return self.hashes.get(k, {}).get(f)

    def hset(self, k, mapping=None):
        self.hashes.setdefault(k, {}).update(mapping or {})

    def hincrby(self, k, f, n):
        self.counts[f] = self.counts.get(f, 0) + n


def test_write_status_increments_on_entering_active():
    r = FakeRedis()
    stmod.write_status(r, "j1", "inspecting")            # old=None -> just incr
    assert r.counts == {"inspecting": 1}
    assert r.hashes["job:j1"]["status"] == "inspecting"


def test_write_status_moves_count_between_active_states():
    r = FakeRedis()
    stmod.write_status(r, "j1", "queued")
    stmod.write_status(r, "j1", "transcoding")           # queued -1, transcoding +1
    assert r.counts == {"queued": 0, "transcoding": 1}


def test_write_status_decrements_active_on_terminal_no_terminal_counter():
    r = FakeRedis()
    stmod.write_status(r, "j1", "transcoding")
    stmod.write_status(r, "j1", "done", extra={"results": "{}"})   # transcoding -1; done not tracked (PG owns it)
    assert r.counts == {"transcoding": 0}
    assert "done" not in r.counts
    assert r.hashes["job:j1"]["results"] == "{}"


# ---------- GET /status ----------

class FakeAsyncRedis:
    def __init__(self, active, beat, dlq):
        self._active, self._beat, self._dlq = active, beat, dlq

    async def hgetall(self, k):
        return dict(self._active)

    async def get(self, k):
        return self._beat

    async def hlen(self, k):
        return self._dlq


async def _ok_queues():
    return {"transcode": 4, "inspect": 0}


async def _ok_kafka():
    return {"dispatcher": 0, "audit": 2}


async def _ok_disk():
    return {"used_bytes": 1500, "budget_bytes": 10**10, "free_bytes": 10**9, "shedding": False}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(st, "get_client",
                        lambda: FakeAsyncRedis(active={"queued": "3", "transcoding": "2"},
                                               beat="2026-06-17T15:00:00+00:00", dlq=5))
    monkeypatch.setattr(st.db, "count_by_status", lambda: {"done": 7, "failed": 1})
    monkeypatch.setitem(st._SECTIONS, "queues", _ok_queues)
    monkeypatch.setitem(st._SECTIONS, "kafka_lag", _ok_kafka)
    monkeypatch.setitem(st._SECTIONS, "disk", _ok_disk)            # /data absent on the host test runner
    monkeypatch.setattr(st, "_cache", {"at": 0.0, "data": None})   # fresh cache per test
    return TestClient(app, raise_server_exceptions=False)


def test_status_aggregates_all_sections(client):
    body = client.get("/status").json()
    assert body["jobs"] == {"awaiting_choice": 0, "inspecting": 0, "queued": 3, "transcoding": 2,
                            "done": 7, "failed": 1}        # redis active + postgres terminal merged
    assert body["dlq"] == {"depth": 5}
    assert body["dispatcher"]["alive"] is True
    assert body["queues"] == {"transcode": 4, "inspect": 0}
    assert body["kafka_lag"] == {"dispatcher": 0, "audit": 2}
    assert body["disk"] == {"used_bytes": 1500, "budget_bytes": 10**10,
                            "free_bytes": 10**9, "shedding": False}


def test_status_degrades_one_section_without_failing_the_rest(client, monkeypatch):
    async def boom():
        raise RuntimeError("rabbitmq down")
    monkeypatch.setitem(st._SECTIONS, "queues", boom)
    monkeypatch.setattr(st, "_cache", {"at": 0.0, "data": None})
    body = client.get("/status").json()
    assert body["queues"] == "unreachable"                # the broken section degrades
    assert body["jobs"]["done"] == 7 and body["dlq"]["depth"] == 5   # the rest still serve


def test_status_dispatcher_dead_when_heartbeat_missing(monkeypatch):
    monkeypatch.setattr(st, "get_client", lambda: FakeAsyncRedis(active={}, beat=None, dlq=0))
    monkeypatch.setattr(st.db, "count_by_status", lambda: {})
    monkeypatch.setitem(st._SECTIONS, "queues", _ok_queues)
    monkeypatch.setitem(st._SECTIONS, "kafka_lag", _ok_kafka)
    monkeypatch.setattr(st, "_cache", {"at": 0.0, "data": None})
    body = TestClient(app, raise_server_exceptions=False).get("/status").json()
    assert body["dispatcher"] == {"alive": False, "last_beat": None}


def test_status_active_counts_clamp_negative_drift(monkeypatch):
    monkeypatch.setattr(st, "get_client",
                        lambda: FakeAsyncRedis(active={"queued": "-2"}, beat=None, dlq=0))
    monkeypatch.setattr(st.db, "count_by_status", lambda: {})
    monkeypatch.setitem(st._SECTIONS, "queues", _ok_queues)
    monkeypatch.setitem(st._SECTIONS, "kafka_lag", _ok_kafka)
    monkeypatch.setattr(st, "_cache", {"at": 0.0, "data": None})
    body = TestClient(app, raise_server_exceptions=False).get("/status").json()
    assert body["jobs"]["queued"] == 0                    # negative drift clamped to 0
