import threading
from types import SimpleNamespace

import pytest
import redis
from redis.exceptions import RedisError

from app.core import ratelimit
from app.core.ratelimit import Allowed, RetryIn, acquire


@pytest.fixture
def redis_client():
    client = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
    try:
        client.ping()
    except (RedisError, OSError):
        pytest.skip("redis not reachable on 127.0.0.1:6379")
    client.delete("test:rl")
    yield client
    client.delete("test:rl")


@pytest.mark.parametrize(("limit", "window_seconds"), [(0, 60), (-1, 60), (3, 0), (3, -1)])
def test_acquire_rejects_non_positive_values(monkeypatch, limit, window_seconds):
    def fail_if_called():
        raise AssertionError("Redis must not be called")

    monkeypatch.setattr(ratelimit, "get_sync_client", fail_if_called)
    with pytest.raises(ValueError):
        acquire("test:rl", limit=limit, window_seconds=window_seconds)


def test_acquire_uses_redis_time(monkeypatch, redis_client):
    monkeypatch.setattr(ratelimit, "time", SimpleNamespace(time=lambda: 0), raising=False)
    monkeypatch.setattr(ratelimit, "get_sync_client", lambda: redis_client)

    assert isinstance(acquire("test:rl", limit=1, window_seconds=60), Allowed)

    [(_, score)] = redis_client.zrange("test:rl", 0, 0, withscores=True)
    seconds, microseconds = redis_client.time()
    redis_now_ms = seconds * 1000 + microseconds // 1000
    assert score == pytest.approx(redis_now_ms, abs=1000)


def test_acquire_paces_a_burst(monkeypatch, redis_client):
    monkeypatch.setattr(ratelimit, "get_sync_client", lambda: redis_client)
    results = [acquire("test:rl", limit=3, window_seconds=60) for _ in range(5)]
    allowed = [r for r in results if isinstance(r, Allowed)]
    denied = [r for r in results if isinstance(r, RetryIn)]
    assert len(allowed) == 3 and len(denied) == 2
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
    assert sum(grants) == 5
