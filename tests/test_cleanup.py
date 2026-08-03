import os
from datetime import UTC, datetime

from app.workers.tasks import cleanup

NOW = datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


class FakeRedis:
    def __init__(self):
        self.deleted = []
        self.sets = {}
        self.set_calls = []

    def delete(self, k):
        self.deleted.append(k)

    def smembers(self, k):
        return set(self.sets.get(k, set()))

    def set(self, key, value, ex=None):
        self.set_calls.append((key, value, ex))

    def eval(self, _script, key_count, content_key, _owner):
        assert key_count == 1
        self.deleted.append(content_key)
        return 1


def test_stale_active_job_is_failed_and_its_storage_is_reclaimed(monkeypatch, tmp_path):
    class ActiveRedis(FakeRedis):
        def __init__(self):
            super().__init__()
            self.hash = {
                "status": "transcoding",
                "content_hash": "sha",
                "rendition_ids": "[]",
            }

        def zrangebyscore(self, *_args, **_kwargs):
            return ["j1"]

        def hgetall(self, _key):
            return dict(self.hash)

        def set(self, key, value, ex=None):
            self.cancel = (key, value, ex)

        def zrem(self, *_args):
            pass

    redis = ActiveRedis()
    (tmp_path / "uploads" / "j1").mkdir(parents=True)
    (tmp_path / "output" / "j1").mkdir(parents=True)
    monkeypatch.setattr(cleanup.config, "data_dir", tmp_path)
    monkeypatch.setattr(cleanup, "get_sync_client", lambda: redis)
    monkeypatch.setattr(cleanup, "transition_status", lambda *_a, **_k: "failed")
    projected = []
    monkeypatch.setattr(
        cleanup.terminal_outbox,
        "drain_one",
        lambda _r, job_id: projected.append(job_id) or True,
    )

    failed = cleanup._sweep_stale_active(NOW)

    assert failed == 1 and projected == ["j1"]
    assert not (tmp_path / "uploads" / "j1").exists()
    assert not (tmp_path / "output" / "j1").exists()
    assert redis.cancel[0] == "cancel:j1"
    assert "content:sha" in redis.deleted


def _out(tmp_path, job_id):
    # config.output_dir is a property (data_dir/"output"); set data_dir and build under it
    d = tmp_path / "output" / job_id
    d.mkdir(parents=True)
    return d


# ---------- _expire_outputs ----------

def test_expire_deletes_dir_dedupe_key_marks_and_emits(monkeypatch, tmp_path):
    _out(tmp_path, "j1")
    monkeypatch.setattr(cleanup.config, "data_dir", tmp_path)
    monkeypatch.setattr(cleanup.db, "list_expirable", lambda cutoff: [{"job_id": "j1", "content_hash": "sha1"}])
    marked, emitted = [], []
    monkeypatch.setattr(cleanup.db, "mark_expired", lambda jid, ts: marked.append(jid) or True)
    monkeypatch.setattr(cleanup, "emit", lambda et, jid, p: emitted.append((et, jid)))
    fake = FakeRedis()
    monkeypatch.setattr(cleanup, "get_sync_client", lambda: fake)

    expired, failed = cleanup._expire_outputs(NOW)

    assert expired == 1 and failed == 0
    assert not (tmp_path / "output" / "j1").exists()      # output dir removed
    assert set(fake.deleted) == {"content:sha1", "job:j1"}  # dedupe key + stale hot hash removed
    assert marked == ["j1"] and emitted == [("job.expired", "j1")]


def test_expire_skips_dedupe_delete_when_no_content_hash(monkeypatch, tmp_path):
    _out(tmp_path, "j1")
    monkeypatch.setattr(cleanup.config, "data_dir", tmp_path)
    monkeypatch.setattr(cleanup.db, "list_expirable", lambda cutoff: [{"job_id": "j1", "content_hash": None}])
    monkeypatch.setattr(cleanup.db, "mark_expired", lambda jid, ts: True)
    monkeypatch.setattr(cleanup, "emit", lambda *a: None)
    fake = FakeRedis()
    monkeypatch.setattr(cleanup, "get_sync_client", lambda: fake)

    expired, failed = cleanup._expire_outputs(NOW)

    assert expired == 1 and failed == 0
    assert fake.deleted == ["job:j1"]                     # only the hot hash; no content: delete attempted


def test_expire_does_not_mark_when_deletion_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(cleanup.config, "data_dir", tmp_path)
    monkeypatch.setattr(cleanup.db, "list_expirable", lambda cutoff: [{"job_id": "j1", "content_hash": "s1"}])
    marked = []
    monkeypatch.setattr(cleanup.db, "mark_expired", lambda jid, ts: marked.append(jid) or True)
    monkeypatch.setattr(cleanup, "emit", lambda *a: None)

    class BoomRedis(FakeRedis):
        def delete(self, k):
            raise OSError("disk gone")

    monkeypatch.setattr(cleanup, "get_sync_client", lambda: BoomRedis())

    expired, failed = cleanup._expire_outputs(NOW)

    assert expired == 0 and failed == 1 and marked == []  # leak not papered over as a success; left for retry


def test_expire_skips_emit_when_already_expired(monkeypatch, tmp_path):
    _out(tmp_path, "j1")
    monkeypatch.setattr(cleanup.config, "data_dir", tmp_path)
    monkeypatch.setattr(cleanup.db, "list_expirable", lambda cutoff: [{"job_id": "j1", "content_hash": "sha1"}])
    monkeypatch.setattr(cleanup.db, "mark_expired", lambda jid, ts: False)   # another sweep won the transition
    emitted = []
    monkeypatch.setattr(cleanup, "emit", lambda et, jid, p: emitted.append(et))
    monkeypatch.setattr(cleanup, "get_sync_client", lambda: FakeRedis())

    expired, failed = cleanup._expire_outputs(NOW)
    assert expired == 0 and failed == 0 and emitted == []        # idempotent: no double-expire event


def test_expire_continues_past_a_failing_job(monkeypatch, tmp_path):
    monkeypatch.setattr(cleanup.config, "data_dir", tmp_path)
    monkeypatch.setattr(cleanup.db, "list_expirable",
                        lambda cutoff: [{"job_id": "bad", "content_hash": "s0"},
                                        {"job_id": "ok", "content_hash": "s1"}])

    def mark(jid, ts):
        if jid == "bad":
            raise RuntimeError("db blip")
        return True

    monkeypatch.setattr(cleanup.db, "mark_expired", mark)
    monkeypatch.setattr(cleanup, "emit", lambda *a: None)
    monkeypatch.setattr(cleanup, "get_sync_client", lambda: FakeRedis())

    expired, failed = cleanup._expire_outputs(NOW)
    assert expired == 1 and failed == 1              # "ok" still expired despite "bad" raising (and counted)


# ---------- _sweep_temp_dirs ----------

def test_temp_sweep_removes_old_leaves_recent_and_finals(monkeypatch, tmp_path):
    monkeypatch.setattr(cleanup.config, "data_dir", tmp_path)
    monkeypatch.setattr(cleanup.config, "transcode_max_seconds", 1800)
    job = tmp_path / "output" / "j1"
    job.mkdir(parents=True)
    old = job / "720p.tmp"; old.mkdir()
    recent = job / "480p.tmp"; recent.mkdir()
    final = job / "1080p"; final.mkdir()             # a real rendition dir must never be touched
    old_ts = NOW.timestamp() - 3600                  # older than 1800+60 -> orphaned
    os.utime(old, (old_ts, old_ts))
    recent_ts = NOW.timestamp() - 10                 # mid-encode -> keep
    os.utime(recent, (recent_ts, recent_ts))

    removed = cleanup._sweep_temp_dirs(NOW)

    assert removed == 1
    assert not old.exists() and recent.exists() and final.exists()


def test_temp_sweep_collects_atomic_path_files_not_real_artifacts(monkeypatch, tmp_path):
    monkeypatch.setattr(cleanup.config, "data_dir", tmp_path)
    monkeypatch.setattr(cleanup.config, "transcode_max_seconds", 1800)
    job = tmp_path / "output" / "j1"
    job.mkdir(parents=True)
    tmp_file = job / "web.tmp.mp4"       # atomic_path leftover (a FILE, the else-branch)
    tmp_file.write_bytes(b"x")
    artifact = job / "poster.jpg"        # real artifact: name has no .tmp -> must be left alone
    artifact.write_bytes(b"x")
    old = NOW.timestamp() - 3600
    os.utime(tmp_file, (old, old))
    os.utime(artifact, (old, old))

    removed = cleanup._sweep_temp_dirs(NOW)

    assert removed == 1 and not tmp_file.exists() and artifact.exists()


def test_sweep_forwards_one_now_to_each_helper_and_reports(monkeypatch):
    seen = []
    monkeypatch.setattr(cleanup, "_expire_outputs", lambda now: seen.append(now) or (2, 1))
    monkeypatch.setattr(cleanup, "_sweep_stale_sources", lambda now: seen.append(now) or (3, 7))
    monkeypatch.setattr(cleanup, "_sweep_stale_active", lambda now: 6)
    monkeypatch.setattr(cleanup, "_sweep_temp_dirs", lambda now: seen.append(now) or 4)
    monkeypatch.setattr(cleanup.terminal_outbox, "drain", lambda _r: 5)
    monkeypatch.setattr(cleanup, "get_sync_client", lambda: FakeRedis())

    result = cleanup.sweep()

    assert result == {
        "projected": 5,
        "expired": 2,
        "failed": 1,
        "stale_active": 6,
        "sources": 3,
        "failed_outputs": 7,
        "temps": 4,
    }
    assert len(seen) == 3 and seen[0] == seen[1] == seen[2]   # one `now` captured, shared by all three


def test_terminal_drain_records_beat_delivery(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(cleanup, "get_sync_client", lambda: fake)
    monkeypatch.setattr(cleanup.terminal_outbox, "drain", lambda _r: 2)

    assert cleanup.drain_terminal() == {"projected": 2}
    assert fake.set_calls[0][0] == cleanup.BEAT_HEARTBEAT
    assert fake.set_calls[0][2] == cleanup.BEAT_HEARTBEAT_TTL


# ---------- _sweep_stale_sources ----------

def test_stale_source_sweep_removes_upload_dirs(monkeypatch, tmp_path):
    (tmp_path / "uploads" / "jf").mkdir(parents=True)
    monkeypatch.setattr(cleanup.config, "data_dir", tmp_path)   # uploads_dir = data_dir/"uploads"
    monkeypatch.setattr(
        cleanup.db,
        "list_stale_sources",
        lambda cutoff: [
            {"job_id": "jf", "status": "done"},
            {"job_id": "gone", "status": "done"},
        ],
    )
    fake = FakeRedis()
    monkeypatch.setattr(cleanup, "get_sync_client", lambda: fake)

    removed, failed_outputs = cleanup._sweep_stale_sources(NOW)

    assert removed == 1                       # "jf" removed; "gone" (no dir) silently skipped
    assert failed_outputs == 0
    assert not (tmp_path / "uploads" / "jf").exists()
    assert set(fake.deleted) == {"src:jf", "src:gone"}


def test_stale_storage_sweep_removes_only_failed_outputs(monkeypatch, tmp_path):
    for job_id in ("done", "failed"):
        (tmp_path / "uploads" / job_id).mkdir(parents=True)
        (tmp_path / "output" / job_id).mkdir(parents=True)
    monkeypatch.setattr(cleanup.config, "data_dir", tmp_path)
    monkeypatch.setattr(
        cleanup.db,
        "list_stale_sources",
        lambda cutoff: [
            {"job_id": "done", "status": "done"},
            {"job_id": "failed", "status": "failed"},
        ],
    )
    monkeypatch.setattr(cleanup, "get_sync_client", lambda: FakeRedis())

    sources, failed_outputs = cleanup._sweep_stale_sources(NOW)

    assert (sources, failed_outputs) == (2, 1)
    assert (tmp_path / "output" / "done").exists()
    assert not (tmp_path / "output" / "failed").exists()


def test_stale_source_sweep_keeps_upload_with_active_consumer(monkeypatch, tmp_path):
    upload = tmp_path / "uploads" / "done"
    upload.mkdir(parents=True)
    monkeypatch.setattr(cleanup.config, "data_dir", tmp_path)
    monkeypatch.setattr(
        cleanup.db,
        "list_stale_sources",
        lambda cutoff: [{"job_id": "done", "status": "done"}],
    )
    fake = FakeRedis()
    fake.sets["src:done"] = {"transcribe"}
    monkeypatch.setattr(cleanup, "get_sync_client", lambda: fake)

    assert cleanup._sweep_stale_sources(NOW) == (0, 0)
    assert upload.exists()
