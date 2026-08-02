# pyright: reportPrivateImportUsage=false
from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, NewTopic

from app.core.config import config
from app.events.topics import TOPIC


def ensure_topics() -> None:
    admin = AdminClient({"bootstrap.servers": config.kafka_bootstrap})
    future = admin.create_topics([NewTopic(TOPIC, num_partitions=3, replication_factor=1)])[TOPIC]
    try:
        future.result()
    except KafkaException as exc:
        if not exc.args or exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
            raise
