import logging, signal, time
from confluent_kafka import Consumer, TopicPartition
from redis.exceptions import RedisError
from app.api.utils import now_iso
from app.core.config import config
from app.dispatcher.dispatch import build_and_fire_chord
from app.dispatcher.guard import claim
from app.dispatcher.handler import BadEvent, parse_event, process
from app.events.topics import TOPIC
from app.storage.state import get_sync_client

logger = logging.getLogger(__name__)
_running = True

def _stop(*_):
    global _running
    _running = False

def _heartbeat():
    get_sync_client().set("dispatcher:heartbeat", now_iso(), ex=config.dispatcher_heartbeat_ttl)

def _enqueue(env: dict):
    build_and_fire_chord(env["job_id"], env["payload"]["presets"])

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
    poison = 0
    try:
        while _running:
            msg = consumer.poll(1.0)
            _heartbeat()
            if msg is None:
                continue
            if msg.error():
                logger.warning("consumer error: %s", msg.error())
                continue

            try:
                env = parse_event(msg.value())
            except BadEvent as e:
                poison += 1
                logger.error("poison pill p%s@%s: %s raw=%r",
                             msg.partition(), msg.offset(), e, msg.value())
                consumer.commit(message=msg, asynchronous=False)
                continue

            try:
                action = process(env, claim=claim, enqueue=_enqueue)
            except RedisError:
                logger.error("redis unavailable — stalling on p%s@%s",
                             msg.partition(), msg.offset())
                # a polled non-error message always has topic/partition/offset; stubs say Optional
                consumer.seek(TopicPartition(msg.topic(), msg.partition(), msg.offset()))  # type: ignore[arg-type]
                time.sleep(2)                                       # fail CLOSED: retry same event
                continue

            consumer.commit(message=msg, asynchronous=False)
            logger.info("%s job=%s event_id=%s", action, env.get("job_id"), env.get("event_id"))
    finally:
        consumer.close() 
        logger.info("dispatcher stopped (poison=%d)", poison)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()