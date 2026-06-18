import app.storage.pressure as pressure


def _force_fresh(monkeypatch):
    monkeypatch.setattr(pressure, "_checked_at", 0.0)


def test_our_usage_sums_uploads_and_output(monkeypatch, tmp_path):
    monkeypatch.setattr(pressure.config, "data_dir", tmp_path)
    (tmp_path / "uploads" / "j1").mkdir(parents=True)
    (tmp_path / "output" / "j2").mkdir(parents=True)
    (tmp_path / "uploads" / "j1" / "source.mp4").write_bytes(b"a" * 1000)
    (tmp_path / "output" / "j2" / "360p.m3u8").write_bytes(b"b" * 500)
    assert pressure.our_usage_bytes() == 1500


def test_our_usage_zero_when_dirs_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(pressure.config, "data_dir", tmp_path)
    assert pressure.our_usage_bytes() == 0


def test_is_shedding_on_budget_reached(monkeypatch):
    monkeypatch.setattr(pressure.config, "storage_budget_bytes", 1000)
    monkeypatch.setattr(pressure.config, "storage_min_free_bytes", 100)
    assert pressure.is_shedding(used=1000, free=10_000) is True
    assert pressure.is_shedding(used=999, free=10_000) is False


def test_is_shedding_on_low_free_space(monkeypatch):
    monkeypatch.setattr(pressure.config, "storage_budget_bytes", 10**12)
    monkeypatch.setattr(pressure.config, "storage_min_free_bytes", 100)
    assert pressure.is_shedding(used=0, free=99) is True
    assert pressure.is_shedding(used=0, free=100) is False


def test_under_pressure_true_when_shedding(monkeypatch):
    _force_fresh(monkeypatch)
    monkeypatch.setattr(pressure, "our_usage_bytes", lambda: 0)
    monkeypatch.setattr(pressure, "free_bytes", lambda: 0)
    monkeypatch.setattr(pressure.config, "storage_min_free_bytes", 100)
    monkeypatch.setattr(pressure.config, "storage_budget_bytes", 10**12)
    assert pressure.under_pressure() is True


def test_under_pressure_caches_between_calls(monkeypatch):
    _force_fresh(monkeypatch)
    calls = []
    monkeypatch.setattr(pressure, "our_usage_bytes", lambda: calls.append(1) or 0)
    monkeypatch.setattr(pressure, "free_bytes", lambda: 10**9)
    pressure.under_pressure()
    pressure.under_pressure()
    assert len(calls) == 1


def test_under_pressure_fail_open_when_probe_errors(monkeypatch):
    _force_fresh(monkeypatch)

    def boom():
        raise OSError("cannot stat /data")

    monkeypatch.setattr(pressure, "free_bytes", boom)
    monkeypatch.setattr(pressure, "our_usage_bytes", lambda: 0)
    assert pressure.under_pressure() is False
