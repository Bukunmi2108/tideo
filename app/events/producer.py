from celery.signals import worker_process_init, worker_process_shutdown
from confluent_kafka import Producer

from app.core.config import config
from app.core.logging import get_logger
from app.events.envelope import Envelope
from app.events.topics import TOPIC

log = get_logger()
_producer: Producer | None = None

def _build() -> Producer:
    return Producer({
        "bootstrap.servers": config.kafka_bootstrap,
        "acks": "all",
        "enable.idempotence": True,
        "client.id": Envelope.__dataclass_fields__["producer"].default,
    })

def get_producer() -> Producer:
    global _producer
    if _producer is None:
        _producer = _build()
    return _producer

def _on_delivery(err, msg):
    if err is not None:
        log.error("kafka_delivery_failed", error=str(err))

def publish(env: Envelope) -> None:
    p = get_producer()
    p.produce(TOPIC, key=env.job_id, value=env.to_json(), on_delivery=_on_delivery)
    p.poll(0)   # serve delivery callbacks without blocking

def flush_producer(timeout: float = 5) -> None:
    """Block until buffered events are delivered. Call on process shutdown (worker + API)."""
    if _producer is not None:
        _producer.flush(timeout)

def emit(event_type: str, job_id: str, payload: dict) -> bool:
    """Publish an event without interrupting the caller on failure."""
    try:
        publish(Envelope(event_type, job_id, payload))
        return True
    except Exception:
        log.warning("event_emit_dropped", event_type=event_type, exc_info=True)
        return False

# --- the fork trap: each Celery child gets its own producer ---
@worker_process_init.connect
def _reset_after_fork(**_):
    global _producer
    _producer = None

@worker_process_shutdown.connect
def _flush_on_shutdown(**_):
    flush_producer()
