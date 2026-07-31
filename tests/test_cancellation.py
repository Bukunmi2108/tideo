from app.workers import cancellation


class FakeRedis:
    def __init__(self, *, flag=False, status="transcoding"):
        self.flag = flag
        self.status = status

    def exists(self, _key):
        return self.flag

    def hget(self, _key, _field):
        return self.status


def test_cancel_flag_is_authoritative(monkeypatch):
    monkeypatch.setattr(cancellation, "get_sync_client", lambda: FakeRedis(flag=True))
    assert cancellation.is_cancelled("j1") is True


def test_cancelled_status_survives_flag_expiry(monkeypatch):
    monkeypatch.setattr(cancellation, "get_sync_client", lambda: FakeRedis(status="cancelled"))
    assert cancellation.is_cancelled("j1") is True


def test_cancel_check_fails_open_when_redis_is_unavailable(monkeypatch):
    monkeypatch.setattr(cancellation, "get_sync_client", lambda: (_ for _ in ()).throw(ConnectionError("down")))
    assert cancellation.is_cancelled("j1") is False
