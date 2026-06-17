import json
from datetime import datetime, timezone

import psycopg2
from psycopg2 import errors as pg_errors
from psycopg2.extras import Json, RealDictCursor

from app.core.config import config
from app.core.logging import get_logger

log = get_logger()

_TRANSIENT = (psycopg2.OperationalError, psycopg2.InterfaceError)


DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id           TEXT PRIMARY KEY,
    content_hash     TEXT NOT NULL,
    source_filename  TEXT NOT NULL,
    source_container TEXT, source_video_codec TEXT, source_audio_codec TEXT,
    source_width     INT,  source_height INT,
    source_duration_s NUMERIC, source_bitrate INT,
    presets          JSONB,
    status           TEXT NOT NULL CHECK (status IN ('done','failed','cancelled','expired')),
    error_code       TEXT, error_message TEXT, error_stage TEXT,
    created_at       TIMESTAMPTZ NOT NULL,
    started_at       TIMESTAMPTZ, finished_at TIMESTAMPTZ, expired_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs (status, created_at DESC);
CREATE INDEX IF NOT EXISTS jobs_content_hash   ON jobs (content_hash);

CREATE TABLE IF NOT EXISTS renditions (
    id             BIGSERIAL PRIMARY KEY,
    job_id         TEXT NOT NULL REFERENCES jobs(job_id),
    preset         TEXT NOT NULL,
    file_path      TEXT, output_bytes BIGINT,
    encode_seconds NUMERIC,
    status         TEXT NOT NULL CHECK (status IN ('completed','failed','cancelled')),
    error_message  TEXT,
    UNIQUE (job_id, preset)
);
"""

# DO UPDATE only when the incoming write is the expiry of a non-expired row (done -> expired).
# Every other conflict (a redelivered done/failed/cancelled) is a no-op: terminal states don't regress.
JOBS_UPSERT = """
INSERT INTO jobs (
    job_id, content_hash, source_filename,
    source_container, source_video_codec, source_audio_codec,
    source_width, source_height, source_duration_s, source_bitrate,
    presets, status, error_code, error_message, error_stage,
    created_at, started_at, finished_at, expired_at
) VALUES (
    %(job_id)s, %(content_hash)s, %(source_filename)s,
    %(source_container)s, %(source_video_codec)s, %(source_audio_codec)s,
    %(source_width)s, %(source_height)s, %(source_duration_s)s, %(source_bitrate)s,
    %(presets)s, %(status)s, %(error_code)s, %(error_message)s, %(error_stage)s,
    %(created_at)s, %(started_at)s, %(finished_at)s, %(expired_at)s
)
ON CONFLICT (job_id) DO UPDATE SET
    status = EXCLUDED.status, expired_at = EXCLUDED.expired_at
WHERE EXCLUDED.status = 'expired' AND jobs.status <> 'expired'
"""

RENDITION_UPSERT = """
INSERT INTO renditions (job_id, preset, file_path, output_bytes, encode_seconds, status, error_message)
VALUES (%(job_id)s, %(preset)s, %(file_path)s, %(output_bytes)s, %(encode_seconds)s, %(status)s, %(error_message)s)
ON CONFLICT (job_id, preset) DO NOTHING
"""


def _safe_loads(raw):
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def job_row(job_id: str, rec: dict, *, finished_at: str, expired_at: str | None = None) -> dict:
    """Redis hash -> jobs INSERT params. Pure; tolerates an inspect-failure hash with no source_meta.
    expired_at is set only by the expiry sweep (done -> expired); all other terminals leave it NULL."""
    sm = _safe_loads(rec.get("source_meta"))
    sm = sm if isinstance(sm, dict) else {}
    return {
        "job_id": job_id,
        "content_hash": rec.get("content_hash"),
        "source_filename": rec.get("source_filename"),
        "source_container": sm.get("container"),
        "source_video_codec": sm.get("video_codec"),
        "source_audio_codec": sm.get("audio_codec"),
        "source_width": sm.get("width"),
        "source_height": sm.get("height"),
        "source_duration_s": sm.get("duration"),
        "source_bitrate": sm.get("bitrate"),
        "presets": Json(_safe_loads(rec.get("presets"))),
        "status": rec.get("status"),
        "error_code": rec.get("error_code") or None,
        "error_message": rec.get("error_message") or None,
        "error_stage": rec.get("error_stage") or None,
        "created_at": rec.get("created_at"),
        "started_at": rec.get("started_at") or None,
        "finished_at": finished_at,
        "expired_at": expired_at,
    }


def rendition_rows(job_id: str, results) -> list[dict]:
    """Chord results -> renditions INSERT params. Only completed renditions carry per-preset metrics."""
    rows = []
    for res in results or []:
        if not isinstance(res, dict) or "preset" not in res:
            continue                                          # non-rendition chord member
        preset = res["preset"]
        rows.append({
            "job_id": job_id,
            "preset": preset,
            "file_path": f"{preset}/index.m3u8",
            "output_bytes": res.get("output_bytes"),
            "encode_seconds": res.get("encode_seconds"),
            "status": "completed",
            "error_message": None,
        })
    return rows


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()


def write_terminal(conn, job_params: dict, rendition_params: list[dict]) -> None:
    with conn.cursor() as cur:
        cur.execute(JOBS_UPSERT, job_params)
        for rp in rendition_params:
            cur.execute(RENDITION_UPSERT, rp)
    conn.commit()


def init_schema() -> None:
    """Create the schema at startup (API lifespan + worker-ready). A transient outage is tolerated —
    persist_terminal self-heals on the first write. A DDL/programming error propagates (it's a bug)."""
    try:
        conn = psycopg2.connect(config.postgres_dsn)
    except _TRANSIENT:
        log.error("schema_init_deferred", reason="postgres_unavailable")
        return
    try:
        ensure_schema(conn)
    finally:
        conn.close()


def get_job(job_id: str) -> dict | None:
    """Terminal row for a job, or None. Errors propagate — a read failure must not look like 'no such job'."""
    conn = psycopg2.connect(config.postgres_dsn)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM jobs WHERE job_id = %s", (job_id,))
            return cur.fetchone()
    finally:
        conn.close()


def list_jobs(*, status: str | None = None, limit: int = 20, offset: int = 0) -> list:
    """Cold history, newest-first. Fetches limit+1 so the caller computes has_more without a COUNT."""
    conn = psycopg2.connect(config.postgres_dsn)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            # job_id tiebreaker: created_at isn't unique, so paging could otherwise skip/dupe rows
            if status:
                cur.execute("SELECT * FROM jobs WHERE status = %s ORDER BY created_at DESC, job_id DESC "
                            "LIMIT %s OFFSET %s", (status, limit + 1, offset))
            else:
                cur.execute("SELECT * FROM jobs ORDER BY created_at DESC, job_id DESC LIMIT %s OFFSET %s",
                            (limit + 1, offset))
            return cur.fetchall()
    finally:
        conn.close()


def list_expirable(cutoff) -> list:
    """done jobs whose retention window has elapsed; job_id + content_hash for output + dedupe cleanup."""
    conn = psycopg2.connect(config.postgres_dsn)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT job_id, content_hash FROM jobs WHERE status = 'done' AND finished_at < %s",
                        (cutoff,))
            return cur.fetchall()
    finally:
        conn.close()


def mark_expired(job_id: str, expired_at) -> bool:
    """done -> expired transition. Returns whether THIS call won it, so re-runs are no-ops and don't re-emit."""
    conn = psycopg2.connect(config.postgres_dsn)
    try:
        with conn.cursor() as cur:
            cur.execute("UPDATE jobs SET status = 'expired', expired_at = %s WHERE job_id = %s AND status = 'done'",
                        (expired_at, job_id))
            won = cur.rowcount > 0
        conn.commit()
        return won
    finally:
        conn.close()


def list_stale_sources(cutoff) -> list:
    """failed/cancelled jobs past the grace window — their source uploads can be reclaimed."""
    conn = psycopg2.connect(config.postgres_dsn)
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT job_id FROM jobs WHERE status IN ('failed','cancelled') AND finished_at < %s",
                        (cutoff,))
            return cur.fetchall()
    finally:
        conn.close()


def persist_terminal(job_id: str, rec: dict, *, results=None, expired_at: str | None = None) -> None:
    """Write the durable terminal row (+ renditions) for a job.

    This is the ONLY store of the full jobs/renditions projection (source metadata, presets, per-rendition
    metrics); the audit `events` log holds only thin payloads and cannot reconstruct it. Fail-OPEN on a
    transient outage so a job that already reached its terminal state in Redis/Kafka isn't crashed by a
    Postgres blip — but that means a transient failure here loses the projection once the Redis hash
    TTL-expires (a retry outbox is the real fix; deferred). A non-transient error is a bug: logged with a
    traceback and still swallowed, since the terminal transition has already happened at the call site."""
    finished_at = datetime.now(timezone.utc).isoformat()
    params = job_row(job_id, rec, finished_at=finished_at, expired_at=expired_at)
    rparams = rendition_rows(job_id, results)
    conn = None
    try:
        conn = psycopg2.connect(config.postgres_dsn)
        try:
            write_terminal(conn, params, rparams)
        except pg_errors.UndefinedTable:
            conn.rollback()
            ensure_schema(conn)
            write_terminal(conn, params, rparams)
    except _TRANSIENT:
        log.error("persist_terminal_skipped", status=params["status"], reason="postgres_unavailable")
    except psycopg2.Error:
        if conn is not None:
            conn.rollback()
        log.error("persist_terminal_failed", status=params["status"], exc_info=True)
    finally:
        if conn is not None:
            conn.close()
