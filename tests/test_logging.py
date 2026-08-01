import io
import json
import logging

import pytest
import structlog

import app.core.logging as L


@pytest.fixture
def cap():
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    L.configure_logging("svc")
    buf = io.StringIO()
    handler = root.handlers[0]
    assert isinstance(handler, logging.StreamHandler)
    handler.setStream(buf)
    yield buf
    L.clear_log_context()
    root.handlers, root.level = saved_handlers, saved_level
    structlog.reset_defaults()


def _lines(buf):
    return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]


def test_stdlib_record_renders_shared_json_schema(cap):
    logging.getLogger("anything").warning("plain stdlib message")
    (line,) = _lines(cap)
    assert line["event"] == "plain stdlib message"
    assert line["level"] == "warning"
    assert line["service"] == "svc"
    assert "timestamp" in line


def test_structlog_event_keeps_its_name_and_fields(cap):
    L.get_logger().info("rendition_completed", preset="720p", encode_seconds=24.2)
    (line,) = _lines(cap)
    assert line["event"] == "rendition_completed"
    assert line["preset"] == "720p" and line["encode_seconds"] == 24.2
    assert line["service"] == "svc"


def test_exception_includes_traceback(cap):
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        L.get_logger().exception("failed")

    (line,) = _lines(cap)
    assert "Traceback (most recent call last)" in line["exception"]
    assert "RuntimeError: boom" in line["exception"]


def test_bind_job_attaches_to_every_subsequent_line(cap):
    L.bind_job("j_abc")
    logging.getLogger("x").info("first")
    L.get_logger().info("second")
    lines = _lines(cap)
    assert [ln["job_id"] for ln in lines] == ["j_abc", "j_abc"]


def test_clear_removes_the_binding(cap):
    L.bind_job("j_abc")
    L.clear_log_context()
    logging.getLogger("x").info("after clear")
    (line,) = _lines(cap)
    assert "job_id" not in line


def test_no_job_id_when_never_bound(cap):
    logging.getLogger("x").info("unbound")
    assert "job_id" not in _lines(cap)[0]
