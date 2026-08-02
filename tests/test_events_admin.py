import pytest
from confluent_kafka import KafkaError, KafkaException

from app.events import admin
from app.events.topics import TOPIC


class Future:
    def __init__(self, error=None):
        self.error = error
        self.resolved = False

    def result(self):
        self.resolved = True
        if self.error is not None:
            raise self.error


class Admin:
    def __init__(self, future):
        self.future = future
        self.created = []

    def create_topics(self, topics):
        self.created.extend(topics)
        return {TOPIC: self.future}


def test_ensure_topics_waits_for_creation(monkeypatch):
    future = Future()
    fake = Admin(future)
    monkeypatch.setattr(admin, "AdminClient", lambda _config: fake)

    admin.ensure_topics()

    assert future.resolved is True
    assert len(fake.created) == 1


def test_ensure_topics_tolerates_already_exists_race(monkeypatch):
    error = KafkaException(KafkaError(KafkaError.TOPIC_ALREADY_EXISTS))
    fake = Admin(Future(error))
    monkeypatch.setattr(admin, "AdminClient", lambda _config: fake)

    admin.ensure_topics()


def test_ensure_topics_surfaces_other_kafka_errors(monkeypatch):
    error = KafkaException(KafkaError(KafkaError.INVALID_CONFIG))
    fake = Admin(Future(error))
    monkeypatch.setattr(admin, "AdminClient", lambda _config: fake)

    with pytest.raises(KafkaException):
        admin.ensure_topics()
