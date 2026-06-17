import json

from psycopg2.extras import Json

import app.storage.db as db
from app.storage.db import (
    DDL,
    JOBS_UPSERT,
    job_row,
    rendition_rows,
    write_terminal,
)

META = {
    "container": "mov,mp4,m4a,3gp,3g2,mj2", "video_codec": "h264", "audio_codec": "aac",
    "width": 1920, "height": 1080, "duration": 12.5, "bitrate": 4500000,
    "fps": 30.0, "has_audio": True, "video_streams": 1, "audio_streams": 1,
}


def _done_rec(**kw):
    rec = {
        "status": "done",
        "content_hash": "sha-abc",
        "source_filename": "clip.mov",
        "source_meta": json.dumps(META),
        "presets": json.dumps(["1080p", "720p"]),
        "created_at": "2026-06-17T10:00:00+00:00",
        "started_at": "2026-06-17T10:00:05+00:00",
    }
    rec.update(kw)
    return rec


# ---------- job_row (pure) ----------

def test_job_row_maps_source_metadata_columns():
    row = job_row("j1", _done_rec(), finished_at="2026-06-17T10:01:00+00:00")
    assert row["job_id"] == "j1"
    assert row["status"] == "done"
    assert row["content_hash"] == "sha-abc"
    assert row["source_filename"] == "clip.mov"
    assert row["source_container"] == META["container"]
    assert row["source_video_codec"] == "h264"
    assert row["source_audio_codec"] == "aac"
    assert row["source_width"] == 1920 and row["source_height"] == 1080
    assert row["source_duration_s"] == 12.5
    assert row["source_bitrate"] == 4500000
    assert row["created_at"] == "2026-06-17T10:00:00+00:00"
    assert row["started_at"] == "2026-06-17T10:00:05+00:00"
    assert row["finished_at"] == "2026-06-17T10:01:00+00:00"
    assert row["expired_at"] is None


def test_job_row_wraps_presets_as_jsonb():
    row = job_row("j1", _done_rec(), finished_at="t")
    assert isinstance(row["presets"], Json)


def test_job_row_tolerates_inspect_failure_without_source_meta():
    # an inspect failure never wrote source_meta/presets/started_at — row must still build, columns NULL
    rec = {
        "status": "failed", "content_hash": "sha-x", "source_filename": "broken.mkv",
        "created_at": "2026-06-17T09:00:00+00:00",
        "error_code": "SOURCE_CORRUPT", "error_message": "moov atom not found", "error_stage": "inspect",
    }
    row = job_row("j2", rec, finished_at="t")
    assert row["status"] == "failed"
    assert row["source_container"] is None and row["source_width"] is None
    assert row["started_at"] is None
    assert row["error_code"] == "SOURCE_CORRUPT" and row["error_stage"] == "inspect"
    assert isinstance(row["presets"], Json)        # Json(None) -> SQL NULL


def test_job_row_tolerates_corrupt_source_meta():
    row = job_row("j3", _done_rec(source_meta="{not json"), finished_at="t")
    assert row["source_container"] is None         # unparseable -> degrade to NULLs, no raise


def test_job_row_blank_error_fields_become_null():
    row = job_row("j4", _done_rec(error_code="", error_message=""), finished_at="t")
    assert row["error_code"] is None and row["error_message"] is None


# ---------- rendition_rows (pure) ----------

def test_rendition_rows_from_chord_results():
    results = [
        {"status": "ok", "preset": "1080p", "output_bytes": 1000, "encode_seconds": 58.0},
        {"status": "ok", "preset": "720p", "output_bytes": 500, "encode_seconds": 40.0},
    ]
    rows = rendition_rows("j1", results)
    assert [r["preset"] for r in rows] == ["1080p", "720p"]
    assert rows[0]["output_bytes"] == 1000 and rows[0]["encode_seconds"] == 58.0
    assert rows[0]["file_path"] == "1080p/index.m3u8"
    assert all(r["status"] == "completed" for r in rows)


def test_rendition_rows_skips_non_rendition_members_and_none():
    assert rendition_rows("j1", None) == []
    rows = rendition_rows("j1", [{"status": "package ok"}, {"preset": "480p", "output_bytes": 1}])
    assert [r["preset"] for r in rows] == ["480p"]


# ---------- write_terminal (I/O against a fake conn) ----------

class FakeCursor:
    def __init__(self, log):
        self._log = log

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self._log.append((sql, params))


class FakeConn:
    def __init__(self):
        self.executed = []
        self.commits = 0

    def cursor(self):
        return FakeCursor(self.executed)

    def commit(self):
        self.commits += 1


def test_write_terminal_inserts_job_then_each_rendition_and_commits():
    conn = FakeConn()
    jp = job_row("j1", _done_rec(), finished_at="t")
    rp = rendition_rows("j1", [{"preset": "1080p", "output_bytes": 1, "encode_seconds": 1.0}])
    write_terminal(conn, jp, rp)
    assert conn.executed[0][0] is JOBS_UPSERT       # jobs row first (renditions FK it)
    assert len(conn.executed) == 2                  # 1 job + 1 rendition
    assert conn.commits == 1


def test_jobs_upsert_retains_expiry_precedence_clause():
    # Guards against silently deleting the WHERE clause. The actual ON CONFLICT *semantics*
    # (redelivered terminal = no-op; done->expired = update) need a real DB -> test_db_integration.py.
    assert "EXCLUDED.status = 'expired' AND jobs.status <> 'expired'" in JOBS_UPSERT


def test_job_row_sets_expired_at_when_provided():
    row = job_row("j1", _done_rec(), finished_at="t", expired_at="2026-06-24T00:00:00+00:00")
    assert row["expired_at"] == "2026-06-24T00:00:00+00:00"


# ---------- persist_terminal: fail-open posture (transient swallowed, bug surfaced, schema self-heals) ----------

class _RaisingCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append(sql)
        if sql is DDL:
            self.conn.healed = True            # ensure_schema ran -> the retry write succeeds
            return
        if sql is JOBS_UPSERT and self.conn.fail_with is not None and not self.conn.healed:
            raise self.conn.fail_with


class _FakeConn:
    """Connection whose jobs-write raises `fail_with` until ensure_schema(DDL) runs (self-heal)."""

    def __init__(self, fail_with=None):
        self.fail_with = fail_with
        self.healed = False
        self.executed = []
        self.commits = 0
        self.rolled_back = 0

    def cursor(self):
        return _RaisingCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        pass


def test_persist_terminal_swallows_transient_outage(monkeypatch):
    import psycopg2
    monkeypatch.setattr(db.psycopg2, "connect",
                        lambda dsn: (_ for _ in ()).throw(psycopg2.OperationalError("down")))
    db.persist_terminal("j1", _done_rec())   # must NOT raise — the job already finished


def test_persist_terminal_swallows_programming_bug(monkeypatch):
    import psycopg2
    conn = _FakeConn(fail_with=psycopg2.ProgrammingError("bad SQL"))   # not UndefinedTable -> no self-heal
    monkeypatch.setattr(db.psycopg2, "connect", lambda dsn: conn)
    db.persist_terminal("j1", _done_rec())    # logged with a traceback, but must NOT raise
    assert conn.rolled_back == 1 and conn.executed.count(JOBS_UPSERT) == 1   # failed once, no retry


def test_persist_terminal_self_heals_missing_tables(monkeypatch):
    from psycopg2 import errors as pg_errors
    conn = _FakeConn(fail_with=pg_errors.UndefinedTable("relation \"jobs\" does not exist"))
    monkeypatch.setattr(db.psycopg2, "connect", lambda dsn: conn)
    db.persist_terminal("j1", _done_rec(),
                        results=[{"preset": "720p", "output_bytes": 1, "encode_seconds": 1.0}])
    assert conn.healed                        # ensure_schema ran (DDL executed)
    assert conn.rolled_back == 1              # aborted txn rolled back before retry
    assert conn.executed.count(JOBS_UPSERT) == 2   # failed once, retried after creating the schema
