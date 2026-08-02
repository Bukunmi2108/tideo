from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.storage.paths import atomic_dir, atomic_path, ensure_output_dir, output_dir


def test_output_dir_lookup_does_not_create_directory(monkeypatch, tmp_path):
    monkeypatch.setattr("app.storage.paths.config.data_dir", tmp_path)

    path = output_dir("j1")

    assert path == tmp_path / "output" / "j1"
    assert not path.exists()


def test_ensure_output_dir_creates_directory(monkeypatch, tmp_path):
    monkeypatch.setattr("app.storage.paths.config.data_dir", tmp_path)

    path = ensure_output_dir("j1")

    assert path.is_dir()


def test_atomic_path_renames_on_success(tmp_path):
    final = tmp_path / "out.mp4"
    with atomic_path(final) as tmp:
        assert tmp.name.startswith("out.tmp.") and tmp.suffix == ".mp4"
        tmp.write_text("data")
    assert final.read_text() == "data"
    assert not tmp.exists()

def test_atomic_path_cleans_temp_on_failure(tmp_path):
    final = tmp_path / "out.mp4"
    tmp_seen = {}
    with pytest.raises(RuntimeError), atomic_path(final) as tmp:
        tmp_seen["p"] = tmp
        tmp.write_text("partial")
        raise RuntimeError("boom")
    assert not final.exists()          # no half-written final
    assert not tmp_seen["p"].exists()  # temp cleaned up


def test_atomic_path_supports_concurrent_writers(tmp_path):
    final = tmp_path / "out.mp4"
    barrier = Barrier(2)

    def write(value):
        with atomic_path(final) as tmp:
            tmp.write_text(value)
            barrier.wait()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, value) for value in ("one", "two")]
        for future in futures:
            future.result()

    assert final.read_text() in {"one", "two"}
    assert not list(tmp_path.glob("out.tmp.*.mp4"))


def test_atomic_dir_supports_concurrent_writers(tmp_path):
    final = tmp_path / "720p"
    barrier = Barrier(2)

    def write(value):
        with atomic_dir(final) as tmp:
            (tmp / "index.m3u8").write_text(value)
            barrier.wait()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(write, value) for value in ("one", "two")]
        for future in futures:
            future.result()

    assert (final / "index.m3u8").read_text() in {"one", "two"}
    assert not list(tmp_path.glob("720p.tmp.*"))
