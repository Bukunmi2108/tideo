import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routes import status as st


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
    monkeypatch.setattr(st.db, "count_by_status", dict)
    monkeypatch.setitem(st._SECTIONS, "queues", _ok_queues)
    monkeypatch.setitem(st._SECTIONS, "kafka_lag", _ok_kafka)
    monkeypatch.setattr(st, "_cache", {"at": 0.0, "data": None})
    body = TestClient(app, raise_server_exceptions=False).get("/status").json()
    assert body["dispatcher"] == {"alive": False, "last_beat": None}


def test_status_active_counts_clamp_negative_drift(monkeypatch):
    monkeypatch.setattr(st, "get_client",
                        lambda: FakeAsyncRedis(active={"queued": "-2"}, beat=None, dlq=0))
    monkeypatch.setattr(st.db, "count_by_status", dict)
    monkeypatch.setitem(st._SECTIONS, "queues", _ok_queues)
    monkeypatch.setitem(st._SECTIONS, "kafka_lag", _ok_kafka)
    monkeypatch.setattr(st, "_cache", {"at": 0.0, "data": None})
    body = TestClient(app, raise_server_exceptions=False).get("/status").json()
    assert body["jobs"]["queued"] == 0                    # negative drift clamped to 0


def test_disk_probe_failure_is_not_reported_as_safe(monkeypatch):
    from app.storage import pressure

    def fail():
        raise OSError("disk unavailable")

    monkeypatch.setattr(pressure, "our_usage_bytes", fail)

    with pytest.raises(OSError, match="disk unavailable"):
        st._disk_snapshot()


def test_missing_rabbitmq_queue_is_unreachable(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return [{"name": name, "messages": 0} for name in st.QUEUE_NAMES if name != "cleanup"]

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(st.httpx, "AsyncClient", lambda **_kwargs: Client())

    with pytest.raises(RuntimeError, match="cleanup"):
        asyncio.run(st._queues_section())


def test_missing_kafka_topic_is_unreachable(monkeypatch):
    class KafkaConsumer:
        def __init__(self, _config):
            pass

        def list_topics(self, *_args, **_kwargs):
            return SimpleNamespace(topics={})

        def close(self):
            pass

    monkeypatch.setattr(st, "Consumer", KafkaConsumer)

    with pytest.raises(RuntimeError, match="topic missing"):
        st._group_lag("dispatcher")


def test_kafka_lag_starts_at_low_watermark_without_commit(monkeypatch):
    class KafkaConsumer:
        def __init__(self, _config):
            pass

        def list_topics(self, *_args, **_kwargs):
            topic = SimpleNamespace(error=None, partitions={0: object()})
            return SimpleNamespace(topics={st.TOPIC: topic})

        def committed(self, _partitions, **_kwargs):
            return [SimpleNamespace(offset=-1001, error=None)]

        def get_watermark_offsets(self, _partition, **_kwargs):
            return 100, 125

        def close(self):
            pass

    monkeypatch.setattr(st, "Consumer", KafkaConsumer)

    assert st._group_lag("dispatcher") == 25


def test_build_runs_sections_concurrently(monkeypatch):
    async def exercise():
        started = set()
        all_started = asyncio.Event()

        async def section(name):
            started.add(name)
            if len(started) == 2:
                all_started.set()
            await asyncio.wait_for(all_started.wait(), timeout=0.1)
            return {"name": name}

        monkeypatch.setattr(st, "_SECTIONS", {
            "first": lambda: section("first"),
            "second": lambda: section("second"),
        })
        return await st._build()

    body = asyncio.run(exercise())

    assert body["first"] == {"name": "first"}
    assert body["second"] == {"name": "second"}


def test_build_times_out_one_section(monkeypatch):
    async def slow():
        await asyncio.sleep(1)

    monkeypatch.setattr(st, "_SECTIONS", {"slow": slow})
    monkeypatch.setattr(st, "_SECTION_TIMEOUT", 0.01)

    body = asyncio.run(st._build())

    assert body["slow"] == "unreachable"


def test_cache_age_starts_after_build(monkeypatch):
    clock = {"now": 0.0}
    calls = 0

    async def build():
        nonlocal calls
        calls += 1
        clock["now"] = 10.0
        return {"build": calls}

    async def exercise():
        monkeypatch.setattr(st, "_cache_lock", asyncio.Lock())
        first = await st.status()
        clock["now"] = 12.0
        second = await st.status()
        return first, second

    monkeypatch.setattr(st, "_cache", {"at": 0.0, "data": None})
    monkeypatch.setattr(st, "_build", build)
    monkeypatch.setattr(st.time, "monotonic", lambda: clock["now"])

    first, second = asyncio.run(exercise())

    assert first == second == {"build": 1}
    assert calls == 1


def test_cache_coalesces_concurrent_builds(monkeypatch):
    calls = 0

    async def exercise():
        nonlocal calls
        started = asyncio.Event()
        release = asyncio.Event()

        async def build():
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return {"build": calls}

        monkeypatch.setattr(st, "_cache", {"at": 0.0, "data": None})
        monkeypatch.setattr(st, "_cache_lock", asyncio.Lock())
        monkeypatch.setattr(st, "_build", build)
        monkeypatch.setattr(st.time, "monotonic", lambda: 1.0)

        first = asyncio.create_task(st.status())
        await started.wait()
        second = asyncio.create_task(st.status())
        await asyncio.sleep(0)
        release.set()
        return await asyncio.gather(first, second)

    first, second = asyncio.run(exercise())

    assert first == second == {"build": 1}
    assert calls == 1
