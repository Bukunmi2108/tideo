from app.workers.tasks import rendition


def test_cancelled_rendition_never_starts_ffmpeg(monkeypatch):
    monkeypatch.setattr(rendition, "is_cancelled", lambda _jid: True)
    monkeypatch.setattr(
        rendition.subprocess,
        "Popen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("ffmpeg started")),
    )

    assert rendition.rendition("j1", "720p", "/src.mp4", {}) == {
        "status": "cancelled",
        "job_id": "j1",
    }


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

        def eval(self, script, key_count, *args):
            job_key, _counts, expected, target, _old_active, _new_active, *extra = args
            if self.hashes[job_key]["status"] != expected:
                return [0, self.hashes[job_key]["status"]]
            self.hashes[job_key].update({"status": target, **dict(zip(extra[::2], extra[1::2], strict=True))})
            return [1, target]

    fake = FakeRedis()
    monkeypatch.setattr(rendition, "get_sync_client", lambda: fake)
    monkeypatch.setattr(rendition, "emit", lambda *a, **k: None)

    rendition._mark_started("j1")

    assert fake.hashes["job:j1"]["status"] == "transcoding"
    assert "started_at" in fake.hashes["job:j1"] and fake.hashes["job:j1"]["started_at"]


def test_mark_started_second_caller_is_noop(monkeypatch):
    class FakeRedis:
        def __init__(self):
            self.hashes = {"job:j1": {"status": "transcoding"}}

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
