from collections import namedtuple

import app.storage.pressure as pressure

_Usage = namedtuple("_Usage", "total used free")


def _force_fresh(monkeypatch):
    monkeypatch.setattr(pressure, "_checked_at", 0.0)   # bypass the cache so each test reads fresh


def test_usage_pct_computes_used_over_total(monkeypatch):
    monkeypatch.setattr(pressure.shutil, "disk_usage", lambda p: _Usage(100, 90, 10))
    assert pressure.usage_pct() == 90.0


def test_under_pressure_true_at_or_over_watermark(monkeypatch):
    _force_fresh(monkeypatch)
    monkeypatch.setattr(pressure.config, "storage_watermark_pct", 85)
    monkeypatch.setattr(pressure, "usage_pct", lambda: 90.0)
    assert pressure.under_pressure() is True


def test_under_pressure_false_below_watermark(monkeypatch):
    _force_fresh(monkeypatch)
    monkeypatch.setattr(pressure.config, "storage_watermark_pct", 85)
    monkeypatch.setattr(pressure, "usage_pct", lambda: 50.0)
    assert pressure.under_pressure() is False


def test_under_pressure_caches_between_calls(monkeypatch):
    _force_fresh(monkeypatch)
    monkeypatch.setattr(pressure.config, "storage_watermark_pct", 85)
    calls = []
    monkeypatch.setattr(pressure, "usage_pct", lambda: calls.append(1) or 90.0)
    pressure.under_pressure()
    pressure.under_pressure()
    assert len(calls) == 1                              # second call served from the few-second cache


def test_under_pressure_fail_open_when_probe_errors(monkeypatch):
    _force_fresh(monkeypatch)
    monkeypatch.setattr(pressure.config, "storage_watermark_pct", 85)

    def boom():
        raise OSError("cannot stat /data")

    monkeypatch.setattr(pressure, "usage_pct", boom)
    assert pressure.under_pressure() is False           # a stat error must not block all new work
