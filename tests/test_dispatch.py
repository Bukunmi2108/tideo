import json
from types import SimpleNamespace

from app.dispatcher import dispatch
from app.storage.job_control import DispatchReservation


class FakeRedis:
    def __init__(self, hash_):
        self.hashes = {"job:j1": dict(hash_)}
        self.kv = {}
        self.sets = {}
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

    def hincrby(self, k, f, n):
        return None

    def sadd(self, k, *vals):
        self.sets.setdefault(k, set()).update(vals)

    def srem(self, k, *vals):
        self.sets.setdefault(k, set()).difference_update(vals)

    def scard(self, k):
        return len(self.sets.get(k, set()))

    def delete(self, k):
        self.sets.pop(k, None)

    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.kv:
            return None
        self.kv[k] = v
        return True

    def exists(self, k):
        return k in self.kv

    def expire(self, k, ttl):
        return True

    def eval(self, script, key_count, *args):
        if key_count == 1:
            source_key, first, *rest = args
            consumers = self.sets.setdefault(source_key, set())
            if "for i = 2" in script:
                consumers.discard(first)
                consumers.update(rest[:-1])
                return len(consumers)
            if first not in consumers:
                return 0
            consumers.remove(first)
            if not consumers:
                consumers.add(rest[0])
                return 1
            return 0
        job_key, _counts, outbox, _deadlines, expected, target, _old_active, _new_active, job_id, terminal, *extra = args
        rec = self.hashes[job_key]
        if rec.get("status") != expected:
            return [0, rec.get("status", "")]
        rec["status"] = target
        rec.update(dict(zip(extra[::2], extra[1::2], strict=True)))
        if terminal == "1":
            self.sadd(outbox, job_id)
        return [1, target]


SRC_META = json.dumps({"fps": 30.0, "duration": 30.0, "has_audio": True})


def reserve_in_fake(fake):
    def reserve(_r, job_id, plan):
        rec = fake.hashes[f"job:{job_id}"]
        if rec.get("status") != "queued":
            return DispatchReservation("skipped", rec.get("status"), None)
        rec.update({
            "dispatch_event_id": plan.event_id,
            "rendition_ids": json.dumps(plan.rendition_ids),
            "chord_callback_id": plan.callback_id,
            "transcribe_id": plan.transcribe_id or "",
        })
        return DispatchReservation("reserved", "queued", plan)
    return reserve

def test_chord_caps_to_dev_max_renditions(monkeypatch):
    fake = FakeRedis({"status": "queued", "source_path": "/u/source.mp4", "source_meta": SRC_META})
    monkeypatch.setattr(dispatch, "get_sync_client", lambda: fake)
    monkeypatch.setattr(dispatch.config, "dev_max_renditions", 2)
    monkeypatch.setattr(dispatch, "reserve_dispatch", reserve_in_fake(fake))
    task_ids = iter(["r0", "r1", "cb-1"])
    monkeypatch.setattr(dispatch, "uuid", lambda: next(task_ids))

    captured = {}
    def fake_chord(header):
        captured["header_len"] = len(list(header))
        return lambda cb: None

    monkeypatch.setattr(dispatch, "chord", fake_chord)
    monkeypatch.setattr(dispatch, "group", lambda gen: list(gen))   # countable header
    # signatures just need .set(link_error=...) to chain; use a stub
    monkeypatch.setattr(dispatch.celery_app, "signature",
                        lambda *a, **k: type("S", (), {"set": lambda self, **kw: self})())

    result = dispatch.dispatch_job(
        "j1", "evt-1", ["1080p", "720p", "480p", "360p"], False,
    )
    assert result == "dispatched"
    assert captured["header_len"] == 2                       # renditions only, capped to dev_max
    assert fake.hashes["job:j1"]["chord_callback_id"] == "cb-1"
    assert json.loads(fake.hashes["job:j1"]["rendition_ids"]) == ["r0", "r1"]


def test_cancelled_job_is_not_dispatched(monkeypatch):
    fake = FakeRedis({"status": "cancelled", "source_path": "/u/source.mp4", "source_meta": SRC_META})
    monkeypatch.setattr(dispatch, "get_sync_client", lambda: fake)
    monkeypatch.setattr(dispatch, "reserve_dispatch", reserve_in_fake(fake))
    monkeypatch.setattr(
        dispatch,
        "chord",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("cancelled job must not dispatch")),
    )

    assert dispatch.dispatch_job("j1", "evt-1", ["720p"], False) == "skipped"


def test_transcribe_dispatched_alongside_and_marked_processing(monkeypatch):
    fake = FakeRedis({"status": "queued", "source_path": "/u/s.mp4", "source_meta": SRC_META})
    monkeypatch.setattr(dispatch, "get_sync_client", lambda: fake)
    monkeypatch.setattr(dispatch, "reserve_dispatch", reserve_in_fake(fake))
    task_ids = iter(["r0", "cb", "stt"])
    monkeypatch.setattr(dispatch, "uuid", lambda: next(task_ids))
    fired = []

    class Sig:
        def set(self, **kwargs):
            fired.append(("set", kwargs))
            return self

        def apply_async(self):
            fired.append("async")

    monkeypatch.setattr(dispatch.celery_app, "signature",
                        lambda name, args=None: fired.append((name, args)) or Sig())
    monkeypatch.setattr(dispatch, "group", lambda gen: list(gen))
    claims_seen_at_chord = []
    monkeypatch.setattr(
        dispatch,
        "chord",
        lambda header: lambda callback: claims_seen_at_chord.append(set(fake.sets["src:j1"])),
    )

    dispatch.dispatch_job("j1", "evt-1", ["720p"], True)
    assert json.loads(fake.hashes["job:j1"]["subtitles"]) == {"status": "processing"}
    assert ("app.workers.tasks.transcribe.transcribe", ["j1", "/u/s.mp4", json.loads(SRC_META)]) in fired
    assert ("set", {"task_id": "stt"}) in fired
    assert "async" in fired                                   # actually enqueued
    assert claims_seen_at_chord == [{"package", "transcribe"}]
    rendition_options = next(options for tag, options in fired if tag == "set" and options.get("task_id") == "r0")
    assert rendition_options["soft_time_limit"] == 90
    assert rendition_options["time_limit"] == 150


def test_no_transcribe_when_subtitles_not_requested(monkeypatch):
    fake = FakeRedis({"status": "queued", "source_path": "/u/s.mp4", "source_meta": SRC_META})
    monkeypatch.setattr(dispatch, "get_sync_client", lambda: fake)
    monkeypatch.setattr(dispatch, "reserve_dispatch", reserve_in_fake(fake))
    task_ids = iter(["r0", "cb"])
    monkeypatch.setattr(dispatch, "uuid", lambda: next(task_ids))

    class Sig:
        def set(self, **kwargs):
            return self

    names = []
    monkeypatch.setattr(dispatch.celery_app, "signature",
                        lambda name, **_k: names.append(name) or Sig())
    monkeypatch.setattr(dispatch, "group", lambda gen: list(gen))
    monkeypatch.setattr(dispatch, "chord", lambda header: lambda callback: None)

    dispatch.dispatch_job("j1", "evt-1", ["720p"], False)
    assert "subtitles" not in fake.hashes["job:j1"]
    assert dispatch.TRANSCRIBE not in names
    assert fake.sets["src:j1"] == {"package"}


def test_post_chord_transcribe_failure_does_not_redispatch_ladder(monkeypatch):
    fake = FakeRedis({"status": "queued", "source_path": "/u/s.mp4", "source_meta": SRC_META})
    monkeypatch.setattr(dispatch, "get_sync_client", lambda: fake)
    monkeypatch.setattr(dispatch, "reserve_dispatch", reserve_in_fake(fake))
    task_ids = iter(["r0", "cb", "stt"])
    monkeypatch.setattr(dispatch, "uuid", lambda: next(task_ids))

    class Sig:
        def set(self, **kwargs):
            return self

    submitted = []
    monkeypatch.setattr(dispatch.celery_app, "signature", lambda name, **_k: Sig())
    monkeypatch.setattr(dispatch, "group", lambda gen: list(gen))
    monkeypatch.setattr(dispatch, "chord", lambda header: lambda callback: submitted.append("chord"))
    monkeypatch.setattr(fake, "exists", lambda _key: (_ for _ in ()).throw(ConnectionError("redis down")))

    assert dispatch.dispatch_job("j1", "evt-1", ["720p"], True) == "dispatched"
    assert submitted == ["chord"]
    assert json.loads(fake.hashes["job:j1"]["subtitles"])["code"] == "STT_DISPATCH_FAILED"


def test_transcribe_is_not_submitted_when_cancel_wins_after_chord_reservation(monkeypatch):
    fake = FakeRedis({"status": "queued", "source_path": "/u/s.mp4", "source_meta": SRC_META})
    monkeypatch.setattr(dispatch, "get_sync_client", lambda: fake)
    monkeypatch.setattr(dispatch, "reserve_dispatch", reserve_in_fake(fake))
    task_ids = iter(["r0", "cb", "stt"])
    monkeypatch.setattr(dispatch, "uuid", lambda: next(task_ids))

    class Sig:
        def set(self, **kwargs):
            return self

    names = []
    monkeypatch.setattr(dispatch.celery_app, "signature",
                        lambda name, **_k: names.append(name) or Sig())
    monkeypatch.setattr(dispatch, "group", lambda gen: list(gen))

    def cancel_after_chord(_header):
        def submit(_callback):
            fake.hashes["job:j1"]["status"] = "cancelled"
            fake.kv["cancel:j1"] = "1"
        return submit

    monkeypatch.setattr(dispatch, "chord", cancel_after_chord)

    dispatch.dispatch_job("j1", "evt-1", ["720p"], True)

    assert dispatch.TRANSCRIBE not in names
    assert "subtitles" not in fake.hashes["job:j1"]
    assert fake.sets["src:j1"] == {"package"}


# ---------- fail_job (link_error handler) ----------

def test_fail_job_marks_failed_and_revokes_siblings(monkeypatch):
    fake = FakeRedis({"status": "transcoding", "rendition_ids": json.dumps(["r0", "r1"])})
    monkeypatch.setattr(dispatch, "get_sync_client", lambda: fake)
    emitted, revoked = [], []
    persisted = []
    monkeypatch.setattr(dispatch, "emit", lambda et, jid, p: emitted.append((et, jid)))
    monkeypatch.setattr(dispatch.terminal_outbox, "drain_one", lambda r, jid: persisted.append((jid, {})))
    monkeypatch.setattr(dispatch.celery_app.control, "revoke",
                        lambda tid, terminate=False: revoked.append(tid))

    dispatch.fail_job(None, None, None, "j1")

    assert persisted == [("j1", {})]                         # durable row written, no rendition results
    assert fake.hashes["job:j1"]["status"] == "failed"
    assert emitted == [("job.failed", "j1")]
    assert revoked == ["r0", "r1"]                           # siblings revoked
    assert fake.published == [("progress:j1", json.dumps({"event": "terminal"}))]  # wakes live WS
    assert fake.hashes["job:j1"]["error_code"] == "ENCODE_FAILED_TRANSIENT"  # no rendition error stored -> default
    dlq_rec = json.loads(fake.hashes["dlq"]["j1"])                           # final failure lands in the DLQ
    assert dlq_rec["error_code"] == "ENCODE_FAILED_TRANSIENT" and dlq_rec["job_id"] == "j1"


def test_fail_job_uses_package_stage_for_callback_failure(monkeypatch):
    fake = FakeRedis({"status": "transcoding", "rendition_ids": "[]"})
    monkeypatch.setattr(dispatch, "get_sync_client", lambda: fake)
    emitted, persisted = [], []
    monkeypatch.setattr(dispatch, "emit", lambda et, jid, payload: emitted.append((et, jid, payload)))
    monkeypatch.setattr(dispatch.terminal_outbox, "drain_one",
                        lambda r, jid: persisted.append((jid, r.hgetall(f"job:{jid}"))))
    monkeypatch.setattr(dispatch.celery_app.control, "revoke", lambda *_a, **_k: None)
    request = SimpleNamespace(id="cb-1", task=dispatch.PACKAGE, args=["j1"], retries=0)

    dispatch.fail_job(request, None, None, "j1")

    rec = fake.hashes["job:j1"]
    assert rec["error_code"] == "ENCODE_FAILED_TRANSIENT"
    assert rec["error_message"] == "package failed"
    assert rec["error_stage"] == "package"
    assert persisted[0][1]["error_stage"] == "package"
    assert emitted == [("job.failed", "j1", {"error_code": "ENCODE_FAILED_TRANSIENT", "stage": "package"})]
    dlq_rec = json.loads(fake.hashes["dlq"]["cb-1"])
    assert dlq_rec["task"] == dispatch.PACKAGE
    assert dlq_rec["error_stage"] == "package"


def test_fail_job_preserves_classified_error_from_rendition(monkeypatch):
    fake = FakeRedis({
        "status": "transcoding", "rendition_ids": "[]",
        "error_code": "SOURCE_UNSUPPORTED", "error_message": "Decoder not found", "error_stage": "transcode",
    })
    monkeypatch.setattr(dispatch, "get_sync_client", lambda: fake)
    codes = []
    monkeypatch.setattr(dispatch, "emit", lambda et, jid, p: codes.append(p.get("error_code")))
    monkeypatch.setattr(dispatch.terminal_outbox, "drain_one", lambda *a, **k: True)
    monkeypatch.setattr(dispatch.celery_app.control, "revoke", lambda *a, **k: None)

    dispatch.fail_job(None, None, None, "j1")

    assert fake.hashes["job:j1"]["error_code"] == "SOURCE_UNSUPPORTED"  # classifier's verdict kept, not overwritten
    assert codes == ["SOURCE_UNSUPPORTED"]                              # JOB_FAILED carries the real code
    assert json.loads(fake.hashes["dlq"]["j1"])["error_code"] == "SOURCE_UNSUPPORTED"  # DLQ record carries it too


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

    rmod._mark_started("j1")
    rmod._mark_started("j1")

    assert fake.hashes["job:j1"]["status"] == "transcoding"
    assert emitted == ["job.started"]                        # exactly once
