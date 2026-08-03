from pathlib import Path

import pytest

from app.workers.tasks import package as pkg


def test_video_only_variant_does_not_advertise_aac(monkeypatch):
    monkeypatch.setattr(
        pkg,
        "_probe_variant",
        lambda _path: {"profile": "High", "level": 31, "width": 1280, "height": 720},
    )

    variant = pkg._variant("/job", "720p", 1_000_000, 30.0, has_audio=False)

    assert variant.codecs == "avc1.64001f"


def test_non_web_safe_mp4_remuxes_the_finished_top_rendition(monkeypatch, tmp_path):
    output = tmp_path / "web.mp4"
    captured = []

    def run(argv, **_kwargs):
        captured.append(argv)
        Path(argv[-1]).write_bytes(b"mp4")

    monkeypatch.setattr(pkg, "run_process", run)

    remuxed_from_source = pkg._web_mp4(
        "/uploads/source.mkv",
        output,
        web_safe=False,
        top_playlist="/output/720p/index.m3u8",
        cancelled=lambda: False,
    )

    assert remuxed_from_source is False
    assert output.read_bytes() == b"mp4"
    assert captured[0][captured[0].index("-i") + 1] == "/output/720p/index.m3u8"
    assert captured[0][captured[0].index("-c") + 1] == "copy"
    assert "libx264" not in captured[0]


def test_failed_web_mp4_never_replaces_the_final(monkeypatch, tmp_path):
    output = tmp_path / "web.mp4"
    monkeypatch.setattr(
        pkg,
        "run_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("ffmpeg")),
    )

    with pytest.raises(RuntimeError):
        pkg._web_mp4(
            "/uploads/source.mp4",
            output,
            web_safe=True,
            top_playlist="/output/720p/index.m3u8",
            cancelled=lambda: False,
        )

    assert not output.exists()


def test_package_is_a_noop_for_an_already_done_job(monkeypatch):
    """An acks_late redelivery of a finished job must short-circuit BEFORE touching the (now-deleted)
    source — no rebuild, no re-persist, no re-emit. The guard is the only thing preventing a crash."""
    class FakeRedis:
        def hget(self, key, field):
            return "done"

        def hgetall(self, key):
            raise AssertionError("must not read the full record for an already-done job")

    monkeypatch.setattr(pkg, "get_sync_client", lambda: FakeRedis())
    monkeypatch.setattr(pkg, "emit", lambda *a, **k: (_ for _ in ()).throw(AssertionError("emit called")))
    monkeypatch.setattr(pkg.terminal_outbox, "drain_one",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("persist called")))
    monkeypatch.setattr(pkg, "run_process", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ffmpeg called")))

    assert pkg.package(["ignored"], "j1") == {"status": "done", "job_id": "j1"}


def test_package_does_no_artifact_work_after_cancellation(monkeypatch):
    class FakeRedis:
        def hget(self, key, field):
            return "cancelled"

    fake = FakeRedis()
    cleaned = []
    monkeypatch.setattr(pkg, "get_sync_client", lambda: fake)
    monkeypatch.setattr(pkg, "_cancel_package",
                        lambda r, jid, path: cleaned.append((jid, path)) or {"status": "cancelled", "job_id": jid})
    monkeypatch.setattr(pkg, "run_process", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ffmpeg called")))

    assert pkg.package(["ignored"], "j1") == {"status": "cancelled", "job_id": "j1"}
    assert cleaned and cleaned[0][0] == "j1"
