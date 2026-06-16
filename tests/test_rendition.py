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
