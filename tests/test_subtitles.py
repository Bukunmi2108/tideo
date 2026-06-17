import json

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
    S.refresh_master(tmp_path, V, 30.0)                      # idempotent re-run, now with the VTT on disk
    assert 'SUBTITLES="subs"' in (tmp_path / "master.m3u8").read_text()
    assert (tmp_path / "subs.m3u8").exists()


def test_attach_subtitles_noops_before_packaging(tmp_path, monkeypatch):
    monkeypatch.setattr(S.paths, "output_dir", lambda jid: tmp_path)
    (tmp_path / "subtitles.vtt").write_text("WEBVTT\n\n")
    assert S.attach_subtitles("j", 30.0) is False            # no manifest yet -> package will fold it in later
    assert not (tmp_path / "master.m3u8").exists()


def test_attach_subtitles_rewrites_master_when_packaged(tmp_path, monkeypatch):
    monkeypatch.setattr(S.paths, "output_dir", lambda jid: tmp_path)
    (tmp_path / "manifest.json").write_text(json.dumps({"renditions": [
        {"preset": "720p", "bandwidth": 1000, "resolution": "1280x720", "codecs": "avc1.4d401f,mp4a.40.2"}]}))
    (tmp_path / "subtitles.vtt").write_text("WEBVTT\n\n")
    assert S.attach_subtitles("j", 30.0) is True
    assert 'SUBTITLES="subs"' in (tmp_path / "master.m3u8").read_text()
