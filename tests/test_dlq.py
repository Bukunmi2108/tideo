import json

from app.workers import dlq


class FakeRedis:
    def __init__(self):
        self.hashes = {}

    def hset(self, k, mapping=None):
        self.hashes.setdefault(k, {}).update(mapping or {})


def test_add_writes_record_keyed_by_id():
    fake = FakeRedis()
    dlq.add(fake, {"id": "t1", "task": "rendition", "args": ["j1", "720p"], "error_code": "STORAGE_FULL"})
    stored = json.loads(fake.hashes[dlq.DLQ_KEY]["t1"])
    assert stored["task"] == "rendition"
    assert stored["error_code"] == "STORAGE_FULL"


def test_add_is_fail_open():
    class Boom:
        def hset(self, *a, **k):
            raise ConnectionError("redis down")

    dlq.add(Boom(), {"id": "t1"})  # must not raise — a DLQ miss is an ops gap, not a failure
