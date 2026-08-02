from app.workers.tasks import package as pkg


def test_package_is_a_noop_for_an_already_done_job(monkeypatch):
    """An acks_late redelivery of a finished job must short-circuit BEFORE touching the (now-deleted)
    source — no rebuild, no re-persist, no re-emit. The guard is the only thing preventing a crash."""
    class FakeRedis:
        def hget(self, key, field):
            return "done"

        def hgetall(self, key):
            raise AssertionError("must not read the full record for an already-done job")

    monkeypatch.setattr(pkg, "get_sync_client", lambda: FakeRedis())
    monkeypatch.setattr(pkg, "emit", lambda *a, **k: (_ for _ in ()).throw(AssertionError("emit called")))
    monkeypatch.setattr(pkg.terminal_outbox, "drain_one",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("persist called")))
    monkeypatch.setattr(pkg.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ffmpeg called")))

    assert pkg.package(["ignored"], "j1") == {"status": "done", "job_id": "j1"}


def test_package_does_no_artifact_work_after_cancellation(monkeypatch):
    class FakeRedis:
        def hget(self, key, field):
            return "cancelled"

    fake = FakeRedis()
    cleaned = []
    monkeypatch.setattr(pkg, "get_sync_client", lambda: fake)
    monkeypatch.setattr(pkg, "_cancel_package",
                        lambda r, jid, path: cleaned.append((jid, path)) or {"status": "cancelled", "job_id": jid})
    monkeypatch.setattr(pkg.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ffmpeg called")))

    assert pkg.package(["ignored"], "j1") == {"status": "cancelled", "job_id": "j1"}
    assert cleaned and cleaned[0][0] == "j1"
