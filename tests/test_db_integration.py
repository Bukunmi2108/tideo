"""Real-Postgres tests for terminal projection conflict semantics."""
import json
from datetime import UTC, datetime

import psycopg2
import pytest

from app.core.config import config
from app.storage.db import ensure_schema, job_row, write_terminal


def _reachable() -> bool:
    try:
        psycopg2.connect(config.postgres_dsn, connect_timeout=2).close()
        return True
    except psycopg2.Error:
        return False


@pytest.fixture
def conn(monkeypatch):
    # in-container the DSN host ('postgres') resolves; from the host the dev stack maps 5432 to localhost.
    # Flip the config host (not just this connection) so db.py's own per-call connects hit the same DB.
    if not _reachable():
        monkeypatch.setattr(config, "postgres_host", "127.0.0.1")
    if not _reachable():
        pytest.skip("postgres not reachable")
    c = psycopg2.connect(config.postgres_dsn)
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


def test_same_terminal_redelivery_patches_subtitles_without_changing_status(conn):
    write_terminal(conn, job_row("it_2", _rec("done"), finished_at="2026-06-17T10:01:00+00:00"), [])
    rec = {**_rec("done"), "subtitles": json.dumps({"status": "ready"})}
    write_terminal(conn, job_row("it_2", rec, finished_at="2026-06-17T11:00:00+00:00"), [])
    with conn.cursor() as cur:
        cur.execute("SELECT status, subtitles FROM jobs WHERE job_id=%s", ("it_2",))
        status, subtitles = cur.fetchone()
    assert status == "done" and subtitles == {"status": "ready"}


def test_count_by_status_groups_terminal_rows(conn):
    from app.storage.db import count_by_status
    write_terminal(conn, job_row("it_c1", _rec("done"), finished_at="2026-06-17T10:00:00+00:00"), [])
    write_terminal(conn, job_row("it_c2", _rec("done"), finished_at="2026-06-17T10:00:00+00:00"), [])
    write_terminal(conn, job_row("it_c3", _rec("failed"), finished_at="2026-06-17T10:00:00+00:00"), [])
    counts = count_by_status()
    assert counts.get("done", 0) >= 2 and counts.get("failed", 0) >= 1


def test_list_jobs_isolated_by_guest_owner(conn):
    from app.storage.db import list_jobs

    owner_a = {**_rec("done"), "owner_session_hash": "owner-a"}
    owner_b = {**_rec("done"), "owner_session_hash": "owner-b"}
    write_terminal(
        conn,
        job_row("it_owner_a", owner_a, finished_at="2026-06-17T10:01:00+00:00"),
        [],
    )
    write_terminal(
        conn,
        job_row("it_owner_b", owner_b, finished_at="2026-06-17T10:01:00+00:00"),
        [],
    )

    rows = list_jobs(owner_session_hash="owner-a")

    assert "it_owner_a" in [row["job_id"] for row in rows]
    assert "it_owner_b" not in [row["job_id"] for row in rows]


def test_list_expirable_and_mark_expired_round_trip(conn):
    from app.storage.db import list_expirable, mark_expired

    old = "2026-06-01T10:00:00+00:00"        # well before any plausible cutoff
    write_terminal(conn, job_row("it_exp", _rec("done"), finished_at=old), [])
    cutoff = datetime(2026, 6, 10, tzinfo=UTC)
    ids = [r["job_id"] for r in list_expirable(cutoff)]
    assert "it_exp" in ids
    now = datetime(2026, 6, 17, tzinfo=UTC)
    assert mark_expired("it_exp", now) is True      # won the done->expired transition
    assert mark_expired("it_exp", now) is False     # idempotent: already expired, no re-transition
    assert "it_exp" not in [r["job_id"] for r in list_expirable(cutoff)]   # no longer eligible


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
