import psycopg2
import pytest
from psycopg2.extras import Json

from app.dispatcher import audit
from app.dispatcher.audit import event_row, store_event
from app.events.envelope import Envelope


def _env(**kw):
    base = {
        "event_id": "e-1", "event_type": "rendition.completed", "job_id": "j1",
        "timestamp": "2026-06-16T10:00:00+00:00", "schema_version": 1,
        "payload": {"preset": "720p", "output_bytes": 42},
    }
    base.update(kw)
    return base


def test_event_row_maps_all_columns():
    row = event_row(_env())
    assert row["event_id"] == "e-1"
    assert row["event_type"] == "rendition.completed"
    assert row["job_id"] == "j1"
    assert row["ts"] == "2026-06-16T10:00:00+00:00"
    assert row["schema_version"] == 1


def test_event_row_wraps_payload_for_jsonb():
    row = event_row(_env())
    assert isinstance(row["payload"], Json)     # psycopg2 JSONB adapter, not a raw dict


def test_event_row_defaults_schema_version_to_1():
    env = _env()
    del env["schema_version"]
    assert event_row(env)["schema_version"] == 1


def test_event_row_tolerates_missing_payload():
    env = _env()
    del env["payload"]
    row = event_row(env)
    assert isinstance(row["payload"], Json)      # Json(None) -> SQL NULL, still adapter-wrapped


# ---------- store_event: transient vs permanent DB errors ----------

class FakeCursor:
    def __init__(self, raises=None):
        self._raises = raises

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        if self._raises is not None:
            raise self._raises


class FakeConn:
    def __init__(self, raises=None):
        self._raises = raises
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self):
        return FakeCursor(self._raises)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_store_event_success_commits():
    conn = FakeConn()
    assert store_event(conn, _env()) == "stored"
    assert conn.committed and not conn.rolled_back


def test_store_event_permanent_dataerror_is_poison_not_retried():
    # a non-UUID event_id raises DataError at the DB — permanent, must be skippable (not wedge)
    conn = FakeConn(raises=psycopg2.DataError("invalid input syntax for type uuid"))
    assert store_event(conn, _env(event_id="not-a-uuid")) == "poison"
    assert conn.rolled_back


def test_store_event_transient_error_reraises_for_retry():
    # DB unreachable -> propagate so the caller stalls and retries (fail-closed), never drops a row
    conn = FakeConn(raises=psycopg2.OperationalError("could not connect"))
    with pytest.raises(psycopg2.OperationalError):
        store_event(conn, _env())
    assert conn.rolled_back


class Message:
    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value

    def error(self):
        return None

    def topic(self):
        return "media-jobs"

    def partition(self):
        return 1

    def offset(self):
        return 7


class Consumer:
    def __init__(self, message):
        self.message = message
        self.polls = 0
        self.seeks = []
        self.commits = []
        self.closed = False

    def subscribe(self, _topics):
        pass

    def poll(self, _timeout):
        self.polls += 1
        if self.polls == 2:
            audit._running = False
        return self.message

    def seek(self, partition):
        self.seeks.append(partition)

    def commit(self, **kwargs):
        self.commits.append(kwargs)

    def close(self):
        self.closed = True


def test_transient_store_failure_rewinds_and_reconnects_before_commit(monkeypatch):
    event = Envelope("rendition.completed", "j1", {"preset": "720p"})
    consumer = Consumer(Message(event.to_json().encode()))
    connections = [FakeConn(), FakeConn()]
    outcomes = iter((psycopg2.OperationalError("postgres down"), "stored"))

    def store(_conn, _env):
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(audit, "Consumer", lambda _config: consumer)
    monkeypatch.setattr(audit.psycopg2, "connect", lambda _dsn: connections.pop(0))
    monkeypatch.setattr(audit, "ensure_schema", lambda _conn: None)
    monkeypatch.setattr(audit, "store_event", store)
    monkeypatch.setattr(audit.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(audit.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(audit, "_running", True)

    audit.run()

    assert len(consumer.seeks) == 1
    assert consumer.seeks[0].offset == 7
    assert len(consumer.commits) == 1
    assert consumer.closed is True


def test_reconnect_keeps_retrying_while_postgres_is_unavailable(monkeypatch):
    event = Envelope("rendition.completed", "j1", {"preset": "720p"})
    consumer = Consumer(Message(event.to_json().encode()))
    first = FakeConn()
    second = FakeConn()
    connection_results = iter((first, psycopg2.OperationalError("still down"), second))
    store_results = iter((psycopg2.OperationalError("connection lost"), "stored"))
    sleeps = []

    def connect(_dsn):
        result = next(connection_results)
        if isinstance(result, Exception):
            raise result
        return result

    def store(_conn, _env):
        result = next(store_results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(audit, "Consumer", lambda _config: consumer)
    monkeypatch.setattr(audit.psycopg2, "connect", connect)
    monkeypatch.setattr(audit, "ensure_schema", lambda _conn: None)
    monkeypatch.setattr(audit, "store_event", store)
    monkeypatch.setattr(audit.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(audit.time, "sleep", sleeps.append)
    monkeypatch.setattr(audit, "_running", True)

    audit.run()

    assert sleeps == [2, 2]
    assert first.closed is True
    assert second.closed is True
    assert len(consumer.commits) == 1
