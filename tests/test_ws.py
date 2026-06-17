import json
from unittest.mock import AsyncMock
from starlette.testclient import TestClient

from app.api.main import app
from app.api import ws as ws_module


# ---- Fakes ----

class FakeRedis:
    """Async Redis stub for the shared client (hgetall + hget only)."""

    def __init__(self, rec: dict, status_seq: list | None = None):
        self._rec = rec
        self._status_iter = iter(status_seq or [])

    async def hgetall(self, _key: str) -> dict:
        return dict(self._rec)

    async def hget(self, _key: str, field: str) -> str | None:
        if field == "status":
            try:
                return next(self._status_iter)
            except StopIteration:
                return self._rec.get("status")
        return self._rec.get(field)


class FakePubSub:
    """Pub/sub stub that yields a fixed message list then stops."""

    def __init__(self, messages: list[dict]):
        self._messages = messages
        self.subscribed: list[str] = []
        self.unsubscribe = AsyncMock()

    async def subscribe(self, ch: str) -> None:
        self.subscribed.append(ch)

    def listen(self):
        msgs = self._messages

        async def _gen():
            for m in msgs:
                yield m

        return _gen()


class FakePubSubClient:
    def __init__(self, messages: list[dict] | None = None):
        self._ps = FakePubSub(messages or [])
        self.aclose = AsyncMock()

    def pubsub(self) -> FakePubSub:
        return self._ps

    @property
    def ps(self) -> FakePubSub:
        return self._ps


def _msg(preset: str, percent: float) -> dict:
    return {"type": "message", "data": json.dumps({"preset": preset, "percent": percent})}


def _setup(monkeypatch, rec: dict, ps_messages=None, status_seq=None):
    r = FakeRedis(rec, status_seq)
    psc = FakePubSubClient(ps_messages or [])
    monkeypatch.setattr(ws_module, "get_client", lambda: r)
    monkeypatch.setattr(ws_module, "_new_pubsub_client", lambda: psc)
    return TestClient(app), psc


# ---- Tests ----

def test_snapshot_content(monkeypatch):
    c, _ = _setup(
        monkeypatch,
        {"status": "transcoding", "progress:720p": "41.2", "progress:480p": "23.7"},
    )
    with c.websocket_connect("/jobs/j1/progress") as ws:
        frame = ws.receive_json()
    assert frame["type"] == "snapshot"
    assert frame["status"] == "transcoding"
    assert frame["progress"] == {"720p": 41.2, "480p": 23.7}


def test_unknown_job_error_frame(monkeypatch):
    c, _ = _setup(monkeypatch, {})
    with c.websocket_connect("/jobs/j1/progress") as ws:
        frame = ws.receive_json()
    assert frame == {"type": "error", "code": "NOT_FOUND"}


def test_done_job_snapshot_then_state_no_subscribe(monkeypatch):
    c, psc = _setup(monkeypatch, {"status": "done"})
    with c.websocket_connect("/jobs/j1/progress") as ws:
        f1 = ws.receive_json()
        f2 = ws.receive_json()
    assert f1["type"] == "snapshot"
    assert f1["status"] == "done"
    assert f2 == {"type": "state", "status": "done"}
    assert psc.ps.subscribed == []


def test_progress_relay(monkeypatch):
    c, _ = _setup(
        monkeypatch,
        {"status": "transcoding"},
        ps_messages=[_msg("720p", 55.0)],
    )
    with c.websocket_connect("/jobs/j1/progress") as ws:
        ws.receive_json()  # snapshot
        frame = ws.receive_json()
    assert frame == {"type": "progress", "preset": "720p", "percent": 55.0}


def test_terminal_detection_after_progress(monkeypatch):
    c, _ = _setup(
        monkeypatch,
        {"status": "transcoding"},
        ps_messages=[_msg("720p", 55.0)],
        status_seq=["done"],
    )
    with c.websocket_connect("/jobs/j1/progress") as ws:
        f1 = ws.receive_json()  # snapshot
        f2 = ws.receive_json()  # progress
        f3 = ws.receive_json()  # state
    assert f1["type"] == "snapshot"
    assert f2 == {"type": "progress", "preset": "720p", "percent": 55.0}
    assert f3 == {"type": "state", "status": "done"}


def test_subscription_cleanup_on_terminal(monkeypatch):
    c, psc = _setup(
        monkeypatch,
        {"status": "transcoding"},
        ps_messages=[_msg("720p", 55.0)],
        status_seq=["done"],
    )
    with c.websocket_connect("/jobs/j1/progress") as ws:
        ws.receive_json()  # snapshot
        ws.receive_json()  # progress
        ws.receive_json()  # state
    psc.ps.unsubscribe.assert_called_once()
    psc.aclose.assert_called_once()


def test_subscription_cleanup_on_exhaust(monkeypatch):
    # listen exhausts without terminal — cleanup must still run
    c, psc = _setup(monkeypatch, {"status": "transcoding"}, ps_messages=[])
    with c.websocket_connect("/jobs/j1/progress") as ws:
        ws.receive_json()  # snapshot
    psc.ps.unsubscribe.assert_called_once()
    psc.aclose.assert_called_once()


def test_subscription_cleanup_on_done_job(monkeypatch):
    # even when returning early (terminal snapshot), cleanup still runs
    c, psc = _setup(monkeypatch, {"status": "done"})
    with c.websocket_connect("/jobs/j1/progress") as ws:
        ws.receive_json()  # snapshot
        ws.receive_json()  # state
    psc.ps.unsubscribe.assert_called_once()
    psc.aclose.assert_called_once()


def test_pubsub_subscribed_to_correct_channel(monkeypatch):
    c, psc = _setup(monkeypatch, {"status": "transcoding"}, ps_messages=[])
    with c.websocket_connect("/jobs/abc123/progress") as ws:
        ws.receive_json()
    assert psc.ps.subscribed == ["progress:abc123"]


def test_multiple_renditions_in_snapshot(monkeypatch):
    rec = {
        "status": "transcoding",
        "progress:1080p": "10.0",
        "progress:720p": "25.0",
        "progress:480p": "40.0",
        "source_path": "/tmp/src.mp4",  # extra hash fields ignored
    }
    c, _ = _setup(monkeypatch, rec)
    with c.websocket_connect("/jobs/j1/progress") as ws:
        frame = ws.receive_json()
    assert frame["progress"] == {"1080p": 10.0, "720p": 25.0, "480p": 40.0}
    assert "source_path" not in frame["progress"]
