import threading

import pytest

from app.core import ratelimit
from app.core.ratelimit import Allowed, RetryIn, acquire, decide, parse_rate


def test_parse_rate():
    assert parse_rate("3/60") == (3, 60)


# ---- pure sliding-window decision ----

def test_decide_allows_under_limit():
    assert isinstance(decide([1000, 2000], now_ms=3000, window_ms=60_000, limit=3), Allowed)


def test_decide_denies_exactly_at_limit():
    d = decide([1000, 2000, 3000], now_ms=3000, window_ms=60_000, limit=3)
    assert isinstance(d, RetryIn)
    assert d.seconds == pytest.approx((1000 + 60_000 - 3000) / 1000)   # wait until the oldest ages out


def test_decide_ignores_entries_outside_the_window():
    # two old entries have rolled past the window; only one is live -> room under a limit of 3
    assert isinstance(decide([10, 20, 59_000], now_ms=120_000, window_ms=60_000, limit=3), Allowed)


def test_decide_window_rollover_frees_a_slot():
    # at the limit, but the oldest is one ms from expiry -> tiny wait, not a full window
    d = decide([100, 50_000, 59_999], now_ms=60_099, window_ms=60_000, limit=3)
    assert isinstance(d, RetryIn) and d.seconds == pytest.approx(0.001)


# ---- atomic check-and-take against a real Redis (skipped if unreachable) ----

@pytest.fixture
def redis_client():
    import redis
    client = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
    try:
        client.ping()
    except Exception:
        pytest.skip("redis not reachable on 127.0.0.1:6379")
    client.delete("test:rl")
    yield client
    client.delete("test:rl")


def test_acquire_paces_a_burst(monkeypatch, redis_client):
    monkeypatch.setattr(ratelimit, "get_sync_client", lambda: redis_client)
    results = [acquire("test:rl", limit=3, window_seconds=60) for _ in range(5)]
    allowed = [r for r in results if isinstance(r, Allowed)]
    denied = [r for r in results if isinstance(r, RetryIn)]
    assert len(allowed) == 3 and len(denied) == 2          # first 3 take slots, rest are told to wait
    assert all(0 < r.seconds <= 60 for r in denied)


def test_acquire_is_atomic_under_concurrency(monkeypatch, redis_client):
    monkeypatch.setattr(ratelimit, "get_sync_client", lambda: redis_client)
    grants = []
    lock = threading.Lock()

    def worker():
        r = acquire("test:rl", limit=5, window_seconds=60)
        if isinstance(r, Allowed):
            with lock:
                grants.append(1)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(grants) == 5                                  # never more than the limit, despite 20 racers
