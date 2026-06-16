import logging
from confluent_kafka import Producer
from celery.signals import worker_process_init, worker_process_shutdown
from app.core.config import config
from app.events.envelope import Envelope
from app.events.topics import TOPIC

logger = logging.getLogger(__name__)
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
        logger.error("kafka delivery failed: %s", err)

def publish(env: Envelope) -> None:
    p = get_producer()
    p.produce(TOPIC, key=env.job_id, value=env.to_json(), on_delivery=_on_delivery)
    p.poll(0)   # serve delivery callbacks without blocking

def flush_producer(timeout: float = 5) -> None:
    """Block until buffered events are delivered. Call on process shutdown (worker + API)."""
    if _producer is not None:
        _producer.flush(timeout)

# --- the fork trap: each Celery child gets its own producer ---
@worker_process_init.connect
def _reset_after_fork(**_):
    global _producer
    _producer = None

@worker_process_shutdown.connect
def _flush_on_shutdown(**_):
    flush_producer()