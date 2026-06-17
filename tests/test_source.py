from app.workers import source as S


class FakeRedis:
    def __init__(self):
        self.sets = {}
        self.expires = {}

    def sadd(self, k, *vals):
        self.sets.setdefault(k, set()).update(vals)

    def srem(self, k, *vals):
        s = self.sets.setdefault(k, set())
        removed = len(s & set(vals))
        s.difference_update(vals)
        return removed

    def scard(self, k):
        return len(self.sets.get(k, set()))

    def delete(self, k):
        self.sets.pop(k, None)

    def expire(self, k, ttl):
        self.expires[k] = ttl


def _patch_reclaim(monkeypatch):
    reclaimed = []
    monkeypatch.setattr(S.shutil, "rmtree", lambda p: reclaimed.append(str(p)))
    return reclaimed


def test_last_consumer_reclaims_not_the_first(monkeypatch):
    reclaimed = _patch_reclaim(monkeypatch)
    r = FakeRedis()
    S.claim_source(r, "j", "package")
    S.claim_source(r, "j", "transcribe")

    S.release_source(r, "j", "package")              # first release: transcribe still holds the source
    assert reclaimed == []
    assert r.scard("src:j") == 1

    S.release_source(r, "j", "transcribe")           # last release: now reclaim
    assert reclaimed == ["/data/uploads/j"]
    assert "src:j" not in r.sets                      # claim key cleared


def test_reclaim_is_order_independent(monkeypatch):
    reclaimed = _patch_reclaim(monkeypatch)
    r = FakeRedis()
    S.claim_source(r, "j", "package")
    S.claim_source(r, "j", "transcribe")
    S.release_source(r, "j", "transcribe")           # transcribe finishes first this time
    assert reclaimed == []
    S.release_source(r, "j", "package")
    assert reclaimed == ["/data/uploads/j"]


def test_single_consumer_reclaims_immediately(monkeypatch):
    reclaimed = _patch_reclaim(monkeypatch)
    r = FakeRedis()
    S.claim_source(r, "j", "package")                # no subtitles requested -> package is the only consumer
    S.release_source(r, "j", "package")
    assert reclaimed == ["/data/uploads/j"]


def test_double_release_is_a_noop(monkeypatch):
    reclaimed = _patch_reclaim(monkeypatch)
    r = FakeRedis()
    S.claim_source(r, "j", "package")
    S.release_source(r, "j", "package")
    S.release_source(r, "j", "package")              # redelivery (acks_late) — must not double-reclaim or error
    assert reclaimed == ["/data/uploads/j"]


def test_claim_sets_ttl_so_a_dead_task_cant_leak_the_key(monkeypatch):
    _patch_reclaim(monkeypatch)
    r = FakeRedis()
    S.claim_source(r, "j", "package")
    assert r.expires["src:j"] > 0


def test_reclaim_failure_still_clears_key_and_does_not_raise(monkeypatch):
    def boom(_p):
        raise OSError("disk")
    monkeypatch.setattr(S.shutil, "rmtree", boom)
    r = FakeRedis()
    S.claim_source(r, "j", "package")
    S.release_source(r, "j", "package")              # OSError is logged, swallowed; key still cleared
    assert "src:j" not in r.sets
