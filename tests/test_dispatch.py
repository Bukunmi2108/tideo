import json

from app.dispatcher import dispatch


class FakeRedis:
    def __init__(self, hash_):
        self.hashes = {"job:j1": dict(hash_)}
        self.kv = {}
        self.published = []

    def publish(self, channel, message):
        self.published.append((channel, message))

    def hgetall(self, k):
        return dict(self.hashes.get(k, {}))

    def hget(self, k, f):
        return self.hashes.get(k, {}).get(f)

    def hset(self, k, mapping=None):
        self.hashes.setdefault(k, {}).update(mapping or {})
        return len(mapping or {})

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True


SRC_META = json.dumps({"fps": 30.0, "duration": 30.0, "has_audio": True})


# ---------- build_and_fire_chord ----------

def test_chord_caps_to_dev_max_renditions(monkeypatch):
    fake = FakeRedis({"source_path": "/u/source.mp4", "source_meta": SRC_META})
    monkeypatch.setattr(dispatch, "get_sync_client", lambda: fake)
    monkeypatch.setattr(dispatch.config, "dev_max_renditions", 2)

    captured = {}
    # chord(header)(callback) -> fake AsyncResult with .id and .parent.children
    class Res:
        id = "cb-1"
        class parent:
            children = [type("C", (), {"id": "r0"}), type("C", (), {"id": "r1"})]

    def fake_chord(header):
        captured["header_len"] = len(list(header))
        return lambda cb: Res()

    monkeypatch.setattr(dispatch, "chord", fake_chord)
    monkeypatch.setattr(dispatch, "group", lambda gen: list(gen))   # countable header
    # signatures just need .set(link_error=...) to chain; use a stub
    monkeypatch.setattr(dispatch.celery_app, "signature",
                        lambda *a, **k: type("S", (), {"set": lambda self, **kw: self})())

    dispatch.build_and_fire_chord("j1", ["1080p", "720p", "480p", "360p"])  # 4 requested
    assert captured["header_len"] == 2                       # renditions only, capped to dev_max
    assert fake.hashes["job:j1"]["chord_callback_id"] == "cb-1"
    assert json.loads(fake.hashes["job:j1"]["rendition_ids"]) == ["r0", "r1"]


# ---------- fail_job (link_error handler) ----------

def test_fail_job_marks_failed_and_revokes_siblings(monkeypatch):
    fake = FakeRedis({"status": "transcoding", "rendition_ids": json.dumps(["r0", "r1"])})
    monkeypatch.setattr(dispatch, "get_sync_client", lambda: fake)
    emitted, revoked = [], []
    monkeypatch.setattr(dispatch, "emit", lambda et, jid, p: emitted.append((et, jid)))
    monkeypatch.setattr(dispatch.celery_app.control, "revoke",
                        lambda tid, terminate=False: revoked.append(tid))

    dispatch.fail_job(None, None, None, "j1")

    assert fake.hashes["job:j1"]["status"] == "failed"
    assert emitted == [("job.failed", "j1")]
    assert revoked == ["r0", "r1"]                           # siblings revoked
    assert fake.published == [("progress:j1", json.dumps({"event": "terminal"}))]  # wakes live WS
    assert fake.hashes["job:j1"]["error_code"] == "ENCODE_FAILED_TRANSIENT"  # no rendition error stored -> default


def test_fail_job_preserves_classified_error_from_rendition(monkeypatch):
    fake = FakeRedis({
        "status": "transcoding", "rendition_ids": "[]",
        "error_code": "SOURCE_UNSUPPORTED", "error_message": "Decoder not found", "error_stage": "transcode",
    })
    monkeypatch.setattr(dispatch, "get_sync_client", lambda: fake)
    codes = []
    monkeypatch.setattr(dispatch, "emit", lambda et, jid, p: codes.append(p.get("error_code")))
    monkeypatch.setattr(dispatch.celery_app.control, "revoke", lambda *a, **k: None)

    dispatch.fail_job(None, None, None, "j1")

    assert fake.hashes["job:j1"]["error_code"] == "SOURCE_UNSUPPORTED"  # classifier's verdict kept, not overwritten
    assert codes == ["SOURCE_UNSUPPORTED"]                              # JOB_FAILED carries the real code


def test_fail_job_on_already_terminal_is_noop(monkeypatch):
    fake = FakeRedis({"status": "failed", "rendition_ids": "[]"})
    monkeypatch.setattr(dispatch, "get_sync_client", lambda: fake)
    emitted = []
    monkeypatch.setattr(dispatch, "emit", lambda et, jid, p: emitted.append(et))
    monkeypatch.setattr(dispatch.celery_app.control, "revoke", lambda *a, **k: None)

    dispatch.fail_job(None, None, None, "j1")
    assert emitted == []                                     # terminal -> transition drops, no re-emit


# ---------- rendition first-start guard ----------

def test_mark_started_only_first_wins(monkeypatch):
    from app.workers.tasks import rendition as rmod
    fake = FakeRedis({"status": "queued"})
    monkeypatch.setattr(rmod, "get_sync_client", lambda: fake)
    emitted = []
    monkeypatch.setattr(rmod, "emit", lambda et, jid, p: emitted.append(et))

    rmod._mark_started("j1")          # first -> transitions + emits
    rmod._mark_started("j1")          # second -> SET NX loses, skips

    assert fake.hashes["job:j1"]["status"] == "transcoding"
    assert emitted == ["job.started"]                        # exactly once
