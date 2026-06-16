from confluent_kafka.admin import AdminClient, NewTopic  # type: ignore[reportPrivateImportUsage]
from app.core.config import config
from app.events.topics import TOPIC

def ensure_topics() -> None:
    admin = AdminClient({"bootstrap.servers": config.kafka_bootstrap})
    existing = admin.list_topics(timeout=10).topics
    if TOPIC in existing:
        return
    fs = admin.create_topics([NewTopic(TOPIC, num_partitions=3, replication_factor=1)])
    for _, f in fs.items():
        f.result() 