import os
from datetime import datetime, timezone

from app.workers.tasks import cleanup


NOW = datetime(2026, 6, 17, 12, 0, tzinfo=timezone.utc)


class FakeRedis:
    def __init__(self):
        self.deleted = []

    def delete(self, k):
        self.deleted.append(k)


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
    monkeypatch.setattr(cleanup, "_sweep_stale_sources", lambda now: seen.append(now) or 3)
    monkeypatch.setattr(cleanup, "_sweep_temp_dirs", lambda now: seen.append(now) or 4)

    result = cleanup.sweep()

    assert result == {"expired": 2, "failed": 1, "sources": 3, "temps": 4}
    assert len(seen) == 3 and seen[0] == seen[1] == seen[2]   # one `now` captured, shared by all three


# ---------- _sweep_stale_sources ----------

def test_stale_source_sweep_removes_upload_dirs(monkeypatch, tmp_path):
    (tmp_path / "uploads" / "jf").mkdir(parents=True)
    monkeypatch.setattr(cleanup.config, "data_dir", tmp_path)   # uploads_dir = data_dir/"uploads"
    monkeypatch.setattr(cleanup.db, "list_stale_sources", lambda cutoff: [{"job_id": "jf"}, {"job_id": "gone"}])

    removed = cleanup._sweep_stale_sources(NOW)

    assert removed == 1                       # "jf" removed; "gone" (no dir) silently skipped
    assert not (tmp_path / "uploads" / "jf").exists()
