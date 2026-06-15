from pathlib import Path
from unittest.mock import MagicMock
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from app.workers.tasks import transcode as tmod


def test_run_ffmpeg_kills_subprocess_on_soft_limit(monkeypatch):
    proc = MagicMock()
    proc.communicate.side_effect = SoftTimeLimitExceeded()
    monkeypatch.setattr(tmod.subprocess, "Popen", lambda *a, **k: proc)

    with pytest.raises(SoftTimeLimitExceeded):
        tmod._run_ffmpeg(["ffmpeg"])

    proc.kill.assert_called_once()  # the running encode is terminated, not left orphaned


def test_transcode_timeout_classifies_and_cleans_temp(monkeypatch, tmp_path):
    monkeypatch.setattr(tmod.paths, "output_dir", lambda job_id: tmp_path)

    def fake_run(argv):
        Path(argv[-1]).write_text("partial")  # simulate a partly-written temp
        raise SoftTimeLimitExceeded()

    monkeypatch.setattr(tmod, "_run_ffmpeg", fake_run)

    result = tmod.transcode("j", "src.mp4")

    assert result["status"] == "failed"
    assert result["error"]["code"] == tmod.ENCODE_TIMEOUT
    assert not (tmp_path / "480p.mp4").exists()       # no final
    assert not (tmp_path / "480p.tmp.mp4").exists()   # temp cleaned by atomic_path


def test_transcode_failure_classifies_and_cleans_temp(monkeypatch, tmp_path):
    monkeypatch.setattr(tmod.paths, "output_dir", lambda job_id: tmp_path)

    def fake_run(argv):
        Path(argv[-1]).write_text("partial")
        return 1, "moov atom not found"

    monkeypatch.setattr(tmod, "_run_ffmpeg", fake_run)

    result = tmod.transcode("j", "src.mp4")

    assert result["error"]["code"] == tmod.ENCODE_FAILED
    assert not (tmp_path / "480p.tmp.mp4").exists()
