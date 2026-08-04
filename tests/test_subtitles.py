import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from app.domain.playlist import Variant, build_master, build_subtitle_media_playlist
from app.workers import subtitles as S

V = [Variant("720p", 1000, 1280, 720, "avc1.4d401f,mp4a.40.2")]


def test_build_master_without_subs_is_unchanged():
    m = build_master(V)
    assert "EXT-X-MEDIA" not in m and "SUBTITLES=" not in m


def test_build_master_with_subs_adds_media_line_and_variant_attr():
    m = build_master(V, has_subtitles=True)
    assert 'EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs"' in m
    assert 'URI="playlist/subs"' in m
    assert 'SUBTITLES="subs"' in m


def test_subtitle_media_playlist_wraps_the_vtt():
    p = build_subtitle_media_playlist(30.0)
    assert "../subtitles" in p and "#EXT-X-ENDLIST" in p


def test_refresh_master_includes_subs_only_when_vtt_present(tmp_path):
    S.refresh_master(tmp_path, V, 30.0)
    assert "SUBTITLES=" not in (tmp_path / "master.m3u8").read_text()
    assert not (tmp_path / "subs.m3u8").exists()

    (tmp_path / "subtitles.vtt").write_text("WEBVTT\n\n")
    S.refresh_master(tmp_path, V, 30.0, media_start_time=1.4)
    assert 'SUBTITLES="subs"' in (tmp_path / "master.m3u8").read_text()
    assert (tmp_path / "subs.m3u8").exists()
    assert "MPEGTS:126000" in (tmp_path / "subtitles.vtt").read_text()


def test_attach_subtitles_noops_before_packaging(tmp_path, monkeypatch):
    monkeypatch.setattr(S.paths, "output_dir", lambda jid: tmp_path)
    (tmp_path / "subtitles.vtt").write_text("WEBVTT\n\n")
    assert S.attach_subtitles("j", 30.0) is False            # no manifest yet -> package will fold it in later
    assert not (tmp_path / "master.m3u8").exists()


def test_attach_subtitles_rewrites_master_when_packaged(tmp_path, monkeypatch):
    monkeypatch.setattr(S.paths, "output_dir", lambda jid: tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps({"renditions": [
        {"preset": "720p", "bandwidth": 1000, "average_bandwidth": 900,
         "resolution": "1280x720", "codecs": "avc1.4d401f,mp4a.40.2"}],
        "media_start_time": 1.4}))
    (tmp_path / "subtitles.vtt").write_text("WEBVTT\n\n")
    assert S.attach_subtitles("j", 30.0) is True
    assert 'SUBTITLES="subs"' in (tmp_path / "master.m3u8").read_text()
    assert "MPEGTS:126000" in (tmp_path / "subtitles.vtt").read_text()
    assert "AVERAGE-BANDWIDTH=900" in (tmp_path / "master.m3u8").read_text()


def test_attach_subtitles_probes_start_time_for_an_older_manifest(tmp_path, monkeypatch):
    monkeypatch.setattr(S.paths, "output_dir", lambda jid: tmp_path)
    monkeypatch.setattr(S, "_probe_media_start", lambda *_args: 1.4)
    (tmp_path / "manifest.json").write_text(json.dumps({"renditions": [
        {"preset": "720p", "bandwidth": 1000,
         "resolution": "1280x720", "codecs": "avc1.4d401f,mp4a.40.2"}]}))
    (tmp_path / "subtitles.vtt").write_text("WEBVTT\n\n")

    assert S.attach_subtitles("j", 30.0) is True
    assert "MPEGTS:126000" in (tmp_path / "subtitles.vtt").read_text()


def test_refresh_master_cannot_overwrite_new_subtitles_with_stale_playlist(tmp_path, monkeypatch):
    original = S.build_master
    no_subtitles_started = Event()
    release_no_subtitles = Event()

    def delayed_build(variants, *, has_subtitles=False):
        if not has_subtitles:
            no_subtitles_started.set()
            release_no_subtitles.wait(0.2)
        return original(variants, has_subtitles=has_subtitles)

    monkeypatch.setattr(S, "build_master", delayed_build)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(S.refresh_master, tmp_path, V, 30.0, media_start_time=1.4)
        assert no_subtitles_started.wait(1)
        (tmp_path / "subtitles.vtt").write_text("WEBVTT\n\n")
        second = pool.submit(S.refresh_master, tmp_path, V, 30.0, media_start_time=1.4)
        first.result()
        second.result()

    assert 'SUBTITLES="subs"' in (tmp_path / "master.m3u8").read_text()
    assert "MPEGTS:126000" in (tmp_path / "subtitles.vtt").read_text()
