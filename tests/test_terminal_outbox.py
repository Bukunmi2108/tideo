import json

import psycopg2

from app.storage import terminal_outbox
from app.storage.state import TERMINAL_OUTBOX


class FakeRedis:
    def __init__(self, rec=None):
        self.hashes = {"job:j1": rec or {}}
        self.sets = {TERMINAL_OUTBOX: {"j1"}}
        self.expiries = []

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def eval(self, _script, key_count, *args):
        assert key_count == 2
        job_key, outbox_key, job_id, has_subtitles, expected, ttl = args
        current = self.hashes[job_key].get("subtitles")
        if (has_subtitles == "0" and current is not None) or (
            has_subtitles == "1" and current != expected
        ):
            return 0
        self.sets.setdefault(outbox_key, set()).discard(job_id)
        self.expiries.append((job_key, ttl))
        return 1


def _rec(**extra):
    rec = {
        "status": "done",
        "finished_at": "2026-08-02T12:00:00+00:00",
        "content_hash": "sha",
        "source_filename": "clip.mp4",
        "created_at": "2026-08-02T11:00:00+00:00",
    }
    rec.update(extra)
    return rec


def test_failed_projection_stays_pending_without_expiring_hash(monkeypatch):
    redis = FakeRedis(_rec())
    monkeypatch.setattr(
        terminal_outbox.db,
        "persist_terminal",
        lambda *_a, **_k: (_ for _ in ()).throw(psycopg2.OperationalError("down")),
    )

    assert terminal_outbox.drain_one(redis, "j1") is False
    assert redis.sets[TERMINAL_OUTBOX] == {"j1"}
    assert redis.expiries == []


def test_successful_projection_removes_pending_and_expires_hot_hash(monkeypatch):
    results = [{"preset": "720p", "output_bytes": 10, "encode_seconds": 2.0}]
    redis = FakeRedis(_rec(rendition_results=json.dumps(results)))
    writes = []
    monkeypatch.setattr(
        terminal_outbox.db,
        "persist_terminal",
        lambda job_id, rec, *, results=None: writes.append((job_id, rec, results)),
    )
    monkeypatch.setattr(terminal_outbox.config, "output_ttl_days", 7)

    assert terminal_outbox.drain_one(redis, "j1") is True
    assert writes[0][0] == "j1" and writes[0][2] == results
    assert redis.sets[TERMINAL_OUTBOX] == set()
    assert redis.expiries == [("job:j1", 7 * 86400)]


def test_projection_does_not_ack_over_concurrent_subtitle_update(monkeypatch):
    redis = FakeRedis(_rec())
    writes = []

    def persist(_job_id, rec, *, results=None):
        writes.append(rec.get("subtitles"))
        redis.hashes["job:j1"]["subtitles"] = '{"status": "ready"}'

    monkeypatch.setattr(terminal_outbox.db, "persist_terminal", persist)

    assert terminal_outbox.drain_one(redis, "j1") is True
    assert writes == [None]
    assert redis.sets[TERMINAL_OUTBOX] == {"j1"}
    assert redis.expiries == []

    assert terminal_outbox.drain_one(redis, "j1") is True
    assert writes[-1] == '{"status": "ready"}'
    assert redis.sets[TERMINAL_OUTBOX] == set()
