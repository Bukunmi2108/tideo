import asyncio
import json
from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi import WebSocketDisconnect
from starlette.testclient import TestClient

from app.api import ws as ws_module
from app.api.main import app
from app.api.session import hash_session_token

SESSION_TOKEN = "v1." + "A" * 43
OTHER_TOKEN = "v1." + "B" * 43
SESSION_HASH = hash_session_token(SESSION_TOKEN)
AUTH_FRAME = {"type": "auth", "session": SESSION_TOKEN}


class FakeRedis:
    def __init__(
        self,
        rec: dict,
        status_seq: list | None = None,
        *,
        pubsub=None,
        events: list[str] | None = None,
    ):
        self._rec = rec
        self._status_iter = iter(status_seq or [])
        self._pubsub = pubsub
        self._events = events

    async def hgetall(self, _key: str) -> dict:
        if self._events is not None:
            self._events.append("read")
        return dict(self._rec)

    async def hget(self, _key: str, field: str) -> str | None:
        if field == "status":
            try:
                return next(self._status_iter)
            except StopIteration:
                return self._rec.get("status")
        return self._rec.get(field)

    def pubsub(self):
        return self._pubsub


class FakePubSub:
    def __init__(self, messages: list[dict], events: list[str] | None = None):
        self._messages = messages
        self.events = events if events is not None else []
        self.subscribed: list[str] = []
        self.aclose = AsyncMock()

    async def subscribe(self, ch: str) -> None:
        self.events.append("subscribe")
        self.subscribed.append(ch)

    async def get_message(self, *, timeout: float) -> dict | None:
        if self._messages:
            return self._messages.pop(0)
        raise WebSocketDisconnect()


def _msg(preset: str, percent: float) -> dict:
    return {"type": "message", "data": json.dumps({"preset": preset, "percent": percent})}


def _setup(monkeypatch, rec: dict, ps_messages=None, status_seq=None):
    rec = dict(rec)
    if rec:
        rec.setdefault("owner_session_hash", SESSION_HASH)
    events: list[str] = []
    ps = FakePubSub(ps_messages or [], events)
    r = FakeRedis(rec, status_seq, pubsub=ps, events=events)
    monkeypatch.setattr(ws_module, "get_client", lambda: r)
    return TestClient(app), ps


@contextmanager
def _connect(client, path="/jobs/j1/progress"):
    with client.websocket_connect(path) as ws:
        ws.send_json(AUTH_FRAME)
        yield ws


def test_snapshot_content(monkeypatch):
    c, _ = _setup(
        monkeypatch,
        {"status": "transcoding", "progress:720p": "41.2", "progress:480p": "23.7"},
    )
    with _connect(c) as ws:
        frame = ws.receive_json()
    assert frame["type"] == "snapshot"
    assert frame["status"] == "transcoding"
    assert frame["progress"] == {"720p": 41.2, "480p": 23.7}


def test_browser_origin_is_enforced(monkeypatch):
    c, _ = _setup(monkeypatch, {"status": "transcoding"})

    with (
        pytest.raises(WebSocketDisconnect) as exc,
        c.websocket_connect(
            "/jobs/j1/progress",
            headers={"origin": "https://example.invalid"},
        ),
    ):
        pass

    assert exc.value.code == 1008


def test_snapshot_includes_presets(monkeypatch):
    c, _ = _setup(monkeypatch, {"status": "transcoding", "presets": json.dumps(["720p", "480p"])})
    with _connect(c) as ws:
        frame = ws.receive_json()
    assert frame["type"] == "snapshot"
    assert frame["presets"] == ["720p", "480p"]


def test_snapshot_malformed_fields_degrade(monkeypatch):
    c, _ = _setup(
        monkeypatch,
        {
            "status": "transcoding",
            "presets": json.dumps({"720p": True}),
            "progress:720p": "not-a-number",
        },
    )

    with _connect(c) as ws:
        frame = ws.receive_json()

    assert frame["presets"] == []
    assert frame["progress"] == {}


def test_unknown_job_closes_without_disclosing_existence(monkeypatch):
    c, _ = _setup(monkeypatch, {})
    with c.websocket_connect("/jobs/j1/progress") as ws:
        ws.send_json(AUTH_FRAME)
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1008


def test_invalid_auth_frame_is_rejected_before_subscription(monkeypatch):
    c, ps = _setup(monkeypatch, {"status": "transcoding"})
    with c.websocket_connect("/jobs/j1/progress") as ws:
        ws.send_json({"type": "auth", "session": "invalid"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1008
    assert ps.subscribed == []


def test_foreign_session_is_rejected_before_subscription(monkeypatch):
    c, ps = _setup(monkeypatch, {"status": "transcoding"})
    with c.websocket_connect("/jobs/j1/progress") as ws:
        ws.send_json({"type": "auth", "session": OTHER_TOKEN})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
    assert exc.value.code == 1008
    assert ps.subscribed == []


def test_done_job_snapshot_then_state_with_results(monkeypatch):
    c, _ = _setup(
        monkeypatch,
        {
            "status": "done",
            "presets": json.dumps(["720p", "480p"]),
            "source_meta": json.dumps({"duration": 60.0}),
        },
    )
    with _connect(c) as ws:
        f1 = ws.receive_json()
        f2 = ws.receive_json()
    assert f1["type"] == "snapshot"
    assert f1["status"] == "done"
    assert f2["type"] == "state"
    assert f2["status"] == "done"
    assert f2["results"]["playlist"] == "/jobs/j1/playlist"
    assert f2["results"]["presets"] == ["720p", "480p"]
    assert f2["results"]["duration"] == 60.0


def test_failed_job_snapshot_then_state_with_error(monkeypatch):
    c, _ = _setup(
        monkeypatch,
        {
            "status": "failed",
            "error_code": "ENCODE_FAILED_TRANSIENT",
            "error_message": "x264 died",
            "error_stage": "transcode",
        },
    )
    with _connect(c) as ws:
        f1 = ws.receive_json()
        f2 = ws.receive_json()
    assert f1["type"] == "snapshot"
    assert f2["type"] == "state"
    assert f2["status"] == "failed"
    assert f2["error"]["code"] == "ENCODE_FAILED_TRANSIENT"
    assert f2["error"]["stage"] == "transcode"
    assert f2["error"]["retryable"] is True


def test_progress_relay(monkeypatch):
    c, _ = _setup(
        monkeypatch,
        {"status": "transcoding"},
        ps_messages=[_msg("720p", 55.0)],
    )
    with _connect(c) as ws:
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
    with _connect(c) as ws:
        f1 = ws.receive_json()  # snapshot
        f2 = ws.receive_json()  # progress
        f3 = ws.receive_json()  # state
    assert f1["type"] == "snapshot"
    assert f2 == {"type": "progress", "preset": "720p", "percent": 55.0}
    assert f3["type"] == "state"
    assert f3["status"] == "done"
    assert "results" in f3


def test_terminal_poke_without_percent_triggers_state(monkeypatch):
    # the packaging callback pokes the channel with a percent-less message when it
    # flips the job to done; the relay must detect terminal, not forward a progress frame
    c, _ = _setup(
        monkeypatch,
        {"status": "transcoding"},
        ps_messages=[{"type": "message", "data": json.dumps({"event": "terminal"})}],
        status_seq=["done"],
    )
    with _connect(c) as ws:
        f1 = ws.receive_json()  # snapshot
        f2 = ws.receive_json()  # state — no progress frame in between
    assert f1["type"] == "snapshot"
    assert f2["type"] == "state"
    assert f2["status"] == "done"


def test_subscription_cleanup_on_terminal(monkeypatch):
    c, ps = _setup(
        monkeypatch,
        {"status": "transcoding"},
        ps_messages=[_msg("720p", 55.0)],
        status_seq=["done"],
    )
    with _connect(c) as ws:
        ws.receive_json()  # snapshot
        ws.receive_json()  # progress
        ws.receive_json()  # state
    ps.aclose.assert_awaited_once()


def test_subscription_cleanup_on_done_job(monkeypatch):
    c, ps = _setup(monkeypatch, {"status": "done"})
    with _connect(c) as ws:
        ws.receive_json()  # snapshot
        ws.receive_json()  # state
    ps.aclose.assert_awaited_once()


def test_pubsub_subscribed_to_correct_channel(monkeypatch):
    c, ps = _setup(monkeypatch, {"status": "transcoding"}, ps_messages=[])
    with _connect(c, "/jobs/abc123/progress") as ws:
        ws.receive_json()
    assert ps.subscribed == ["progress:abc123"]


def test_subscribes_before_snapshot_read(monkeypatch):
    c, ps = _setup(monkeypatch, {"status": "transcoding"})

    with _connect(c) as ws:
        ws.receive_json()

    assert ps.events[:3] == ["read", "subscribe", "read"]


def test_heartbeat_disconnect_closes_pubsub(monkeypatch):
    class IdlePubSub(FakePubSub):
        async def get_message(self, *, timeout: float) -> dict | None:
            return None

    class DisconnectOnPing:
        @property
        def headers(self):
            return {}

        async def accept(self):
            pass

        async def receive_json(self):
            return AUTH_FRAME

        async def send_json(self, frame):
            if frame["type"] == "ping":
                raise WebSocketDisconnect()

        async def close(self, code):
            pass

    ps = IdlePubSub([])
    redis = FakeRedis(
        {"status": "transcoding", "owner_session_hash": SESSION_HASH},
        pubsub=ps,
    )
    monkeypatch.setattr(ws_module, "get_client", lambda: redis)
    monkeypatch.setattr(ws_module, "PING_INTERVAL", 0)

    async def run():
        await asyncio.wait_for(
            ws_module.progress_ws(DisconnectOnPing(), "j1"),
            timeout=0.05,
        )

    asyncio.run(run())

    ps.aclose.assert_awaited_once()
