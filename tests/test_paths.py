import pytest
from app.storage.paths import atomic_path


def test_atomic_path_renames_on_success(tmp_path):
    final = tmp_path / "out.mp4"
    with atomic_path(final) as tmp:
        assert tmp.name == "out.tmp.mp4"
        tmp.write_text("data")
    assert final.read_text() == "data"
    assert not tmp.exists()

def test_atomic_path_cleans_temp_on_failure(tmp_path):
    final = tmp_path / "out.mp4"
    tmp_seen = {}
    with pytest.raises(RuntimeError):
        with atomic_path(final) as tmp:
            tmp_seen["p"] = tmp
            tmp.write_text("partial")
            raise RuntimeError("boom")
    assert not final.exists()          # no half-written final
    assert not tmp_seen["p"].exists()  # temp cleaned up
