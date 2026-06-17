from app.workers.tasks import rendition


def test_write_progress_is_fail_open(monkeypatch):
    """A Redis hiccup during an encode must be swallowed — the work outlives observability."""
    class Boom:
        def hset(self, *a, **k):
            raise ConnectionError("redis down")

        def publish(self, *a, **k):
            raise ConnectionError("redis down")

    monkeypatch.setattr(rendition, "get_sync_client", lambda: Boom())
    rendition._write_progress("j_x", "720p", 50.0)   # must NOT raise


def test_mark_started_records_started_at(monkeypatch):
    """The queued->transcoding flip stamps started_at on the hash (the source of the Postgres column)."""
    class FakeRedis:
        def __init__(self):
            self.kv, self.hashes = {}, {"job:j1": {"status": "queued"}}

        def set(self, k, v, nx=False):
            if nx and k in self.kv:
                return None
            self.kv[k] = v
            return True

        def hget(self, k, f):
            return self.hashes.get(k, {}).get(f)

        def hset(self, k, mapping=None):
            self.hashes.setdefault(k, {}).update(mapping or {})

        def hincrby(self, k, f, n):
            return None

    fake = FakeRedis()
    monkeypatch.setattr(rendition, "get_sync_client", lambda: fake)
    monkeypatch.setattr(rendition, "emit", lambda *a, **k: None)

    rendition._mark_started("j1")

    assert fake.hashes["job:j1"]["status"] == "transcoding"
    assert "started_at" in fake.hashes["job:j1"] and fake.hashes["job:j1"]["started_at"]


def test_mark_started_second_caller_is_noop(monkeypatch):
    """SET NX picks one winner; a parallel sibling must not re-stamp or re-transition."""
    class FakeRedis:
        def __init__(self):
            self.kv, self.hashes = {"started:j1": "1"}, {"job:j1": {"status": "transcoding"}}

        def set(self, k, v, nx=False):
            if nx and k in self.kv:
                return None
            self.kv[k] = v
            return True

        def hget(self, k, f):
            return self.hashes.get(k, {}).get(f)

        def hset(self, k, mapping=None):
            self.hashes.setdefault(k, {}).update(mapping or {})

        def hincrby(self, k, f, n):
            return None

    fake = FakeRedis()
    emitted = []
    monkeypatch.setattr(rendition, "get_sync_client", lambda: fake)
    monkeypatch.setattr(rendition, "emit", lambda *a, **k: emitted.append(a))

    rendition._mark_started("j1")

    assert "started_at" not in fake.hashes["job:j1"] and emitted == []
