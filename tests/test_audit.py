import psycopg2
import pytest
from psycopg2.extras import Json

from app.dispatcher.audit import event_row, store_event


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

    def cursor(self):
        return FakeCursor(self._raises)

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True


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
