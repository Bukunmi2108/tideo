"""Real-Postgres tests for the ON CONFLICT idempotency/precedence semantics — the one contract a
FakeConn cannot model (conflict resolution lives in the engine). Skips when no DB is reachable, so
it runs against the local dev stack and is a no-op in a DB-less CI. Mirrors the manual drill in
docs/phases (done insert / redelivered no-op / done->expired update)."""
import psycopg2
import pytest

from app.core.config import config
from app.storage.db import ensure_schema, job_row, write_terminal


def _connect():
    # in-container the DSN host ('postgres') resolves; from the host, the dev stack maps 5432 to localhost
    host_dsn = (f"postgresql://{config.postgres_user}:{config.postgres_password}"
                f"@127.0.0.1:{config.postgres_port}/{config.postgres_db}")
    for dsn in (config.postgres_dsn, host_dsn):
        try:
            return psycopg2.connect(dsn, connect_timeout=2)
        except psycopg2.Error:
            continue
    pytest.skip("postgres not reachable")


@pytest.fixture
def conn():
    c = _connect()
    ensure_schema(c)
    with c.cursor() as cur:
        cur.execute("DELETE FROM renditions WHERE job_id LIKE 'it_%'")
        cur.execute("DELETE FROM jobs WHERE job_id LIKE 'it_%'")
    c.commit()
    yield c
    with c.cursor() as cur:
        cur.execute("DELETE FROM renditions WHERE job_id LIKE 'it_%'")
        cur.execute("DELETE FROM jobs WHERE job_id LIKE 'it_%'")
    c.commit()
    c.close()


def _rec(status):
    return {"status": status, "content_hash": "h", "source_filename": "f.mp4",
            "created_at": "2026-06-17T10:00:00+00:00"}


def _status_and_count(conn, job_id):
    with conn.cursor() as cur:
        cur.execute("SELECT status, expired_at FROM jobs WHERE job_id=%s", (job_id,))
        rows = cur.fetchall()
    return rows


def test_redelivered_terminal_is_a_noop(conn):
    write_terminal(conn, job_row("it_1", _rec("done"), finished_at="2026-06-17T10:01:00+00:00"), [])
    # redelivery with a *different* finished_at must not regress or duplicate the row
    write_terminal(conn, job_row("it_1", _rec("done"), finished_at="2026-06-17T11:00:00+00:00"), [])
    rows = _status_and_count(conn, "it_1")
    assert len(rows) == 1 and rows[0][0] == "done"


def test_done_to_expired_updates_and_sets_expired_at(conn):
    write_terminal(conn, job_row("it_2", _rec("done"), finished_at="2026-06-17T10:01:00+00:00"), [])
    write_terminal(conn, job_row("it_2", _rec("expired"), finished_at="2026-06-17T10:01:00+00:00",
                                 expired_at="2026-06-24T00:00:00+00:00"), [])
    rows = _status_and_count(conn, "it_2")
    assert len(rows) == 1 and rows[0][0] == "expired" and rows[0][1] is not None


def test_expiry_redelivery_is_a_noop(conn):
    write_terminal(conn, job_row("it_3", _rec("done"), finished_at="2026-06-17T10:01:00+00:00"), [])
    write_terminal(conn, job_row("it_3", _rec("expired"), finished_at="2026-06-17T10:01:00+00:00",
                                 expired_at="2026-06-24T00:00:00+00:00"), [])
    write_terminal(conn, job_row("it_3", _rec("expired"), finished_at="2026-06-17T10:01:00+00:00",
                                 expired_at="2026-06-30T00:00:00+00:00"), [])   # second expiry: no-op
    with conn.cursor() as cur:
        cur.execute("SELECT expired_at FROM jobs WHERE job_id=%s", ("it_3",))
        expired_at = cur.fetchone()[0]
    assert expired_at.isoformat().startswith("2026-06-24")   # first expiry stuck, not overwritten


def test_renditions_round_trip_and_dont_duplicate_on_redelivery(conn):
    results = [{"preset": "720p", "output_bytes": 500, "encode_seconds": 40.0}]
    from app.storage.db import rendition_rows
    jp = job_row("it_4", _rec("done"), finished_at="2026-06-17T10:01:00+00:00")
    write_terminal(conn, jp, rendition_rows("it_4", results))
    write_terminal(conn, jp, rendition_rows("it_4", results))   # redelivery
    with conn.cursor() as cur:
        cur.execute("SELECT preset, output_bytes, encode_seconds FROM renditions WHERE job_id=%s", ("it_4",))
        rows = cur.fetchall()
    assert rows == [("720p", 500, 40.0)]                        # exactly one, ON CONFLICT DO NOTHING
