import logging
import signal

import psycopg2
from confluent_kafka import Consumer
from psycopg2.extras import Json

from app.core.config import config
from app.core.logging import bind_job, clear_log_context, configure_logging
from app.dispatcher.handler import BadEvent, parse_event
from app.events.topics import TOPIC

logger = logging.getLogger(__name__)
_running = True


def _stop(*_):
    global _running
    _running = False


DDL = """
CREATE TABLE IF NOT EXISTS events (
    event_id        UUID PRIMARY KEY,
    event_type      TEXT NOT NULL,
    job_id          TEXT NOT NULL,
    ts              TIMESTAMPTZ NOT NULL,
    payload         JSONB,
    schema_version  INT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_job_ts ON events (job_id, ts);
"""

INSERT = """
INSERT INTO events (event_id, event_type, job_id, ts, payload, schema_version)
VALUES (%(event_id)s, %(event_type)s, %(job_id)s, %(ts)s, %(payload)s, %(schema_version)s)
ON CONFLICT (event_id) DO NOTHING
"""


def event_row(env: dict) -> dict:
    """Envelope dict -> INSERT params. Pure; unit-testable without a DB."""
    return {
        "event_id": env["event_id"],
        "event_type": env["event_type"],
        "job_id": env["job_id"],
        "ts": env.get("timestamp"),
        "payload": Json(env.get("payload")),
        "schema_version": env.get("schema_version", 1),
    }


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()


# Transient (DB unreachable) -> retrying eventually succeeds. Everything else (e.g. a non-UUID
# event_id) is permanent -> skip rather than retry forever and wedge the partition.
_TRANSIENT = (psycopg2.OperationalError, psycopg2.InterfaceError)


def store_event(conn, env: dict) -> str:
    """Insert one event idempotently. Returns 'stored' or 'poison'; re-raises transient errors
    so the caller stalls and retries (fail-closed)."""
    try:
        with conn.cursor() as cur:
            cur.execute(INSERT, event_row(env))
        conn.commit()
        return "stored"
    except _TRANSIENT:
        conn.rollback()
        raise
    except psycopg2.Error:
        conn.rollback()
        return "poison"


def run():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    conn = psycopg2.connect(config.postgres_dsn)
    ensure_schema(conn)
    consumer = Consumer({
        "bootstrap.servers": config.kafka_bootstrap,
        "group.id": "audit", 
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe([TOPIC])
    poison = 0
    try:
        while _running:
            clear_log_context()
            msg = consumer.poll(1.0)
            if msg is None:
                continue
            if msg.error():
                logger.warning("consumer error: %s", msg.error())
                continue

            try:
                env = parse_event(msg.value())
            except BadEvent as e:
                logger.error("poison pill p%s@%s: %s", msg.partition(), msg.offset(), e)
                consumer.commit(message=msg, asynchronous=False)
                continue

            bind_job(env["job_id"])
            try:
                result = store_event(conn, env)
            except _TRANSIENT:
                logger.error("postgres unavailable — not committing, will retry p%s@%s",
                             msg.partition(), msg.offset())
                continue
            if result == "poison":
                poison += 1
                logger.error("unstorable event p%s@%s event_id=%s — skipping",
                             msg.partition(), msg.offset(), env.get("event_id"))
            consumer.commit(message=msg, asynchronous=False)
            if result == "stored":
                logger.info("audited %s job=%s event_id=%s",
                            env["event_type"], env["job_id"], env["event_id"])
    finally:
        consumer.close()
        conn.close()
        logger.info("audit stopped (poison=%d)", poison)


if __name__ == "__main__":
    configure_logging("audit")
    run()
