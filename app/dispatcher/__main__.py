import signal
import time
from datetime import UTC, datetime

from confluent_kafka import Consumer, TopicPartition
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError

from app.core.config import config
from app.core.logging import bind_job, clear_log_context, configure_logging, get_logger
from app.dispatcher.dispatch import dispatch_job
from app.dispatcher.guard import claim, release
from app.dispatcher.handler import BadEvent, parse_event, process
from app.events.outbox import drain as drain_outbox
from app.events.topics import TOPIC
from app.storage.state import get_sync_client

log = get_logger()
_running = True

def _stop(*_):
    global _running
    _running = False

def _heartbeat():
    get_sync_client().set(
        "dispatcher:heartbeat",
        datetime.now(UTC).isoformat(),
        ex=config.dispatcher_heartbeat_ttl,
    )

def _enqueue(env: dict):
    dispatch_job(
        env["job_id"],
        env["event_id"],
        env["payload"]["presets"],
        env["payload"].get("subtitles", False),
    )

def run():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    consumer = Consumer({
        "bootstrap.servers": config.kafka_bootstrap,
        "group.id": "dispatcher",
        "enable.auto.commit": False,
        "auto.offset.reset": "earliest",
    })
    consumer.subscribe([TOPIC])
    try:
        _heartbeat()
        next_heartbeat = time.monotonic() + config.dispatcher_heartbeat_ttl / 3
        while _running:
            clear_log_context()
            try:
                drain_outbox(get_sync_client())
            except RedisError:
                log.exception("event_outbox_unavailable")
            msg = consumer.poll(1.0)
            now = time.monotonic()
            if now >= next_heartbeat:
                _heartbeat()
                next_heartbeat = now + config.dispatcher_heartbeat_ttl / 3
            if msg is None:
                continue
            if msg.error():
                log.warning("consumer_error", error=str(msg.error()))
                continue

            try:
                env = parse_event(msg.value())
            except BadEvent as e:
                log.error("poison_pill", partition=msg.partition(), offset=msg.offset(), error=str(e), raw=str(msg.value()))
                consumer.commit(message=msg, asynchronous=False)
                continue

            bind_job(env["job_id"])
            try:
                action = process(env, claim=claim, enqueue=_enqueue, release=release)
            except (RedisError, OperationalError) as e:
                # infra down (redis claim, or broker enqueue). The claim (if taken) was released,
                # so the re-consumed event retries cleanly. Fail CLOSED: don't commit, re-poll same event.
                log.error("infra_unavailable", error_type=type(e).__name__, partition=msg.partition(), offset=msg.offset())
                # a polled non-error message always has topic/partition/offset; stubs say Optional
                consumer.seek(TopicPartition(msg.topic(), msg.partition(), msg.offset()))  # type: ignore[arg-type]
                time.sleep(2)
                continue

            consumer.commit(message=msg, asynchronous=False)
            log.info("event_processed", action=action, event_id=env.get("event_id"))
    finally:
        consumer.close()
        log.info("dispatcher_stopped")

if __name__ == "__main__":
    configure_logging("dispatcher")
    run()
