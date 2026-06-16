import pytest

from app.core.config import config
from app.domain.ladder import PRESETS
from app.workers.ffmpeg import build_rendition_argv
from app.workers.ffprobe import SourceMeta


def meta(**kw) -> SourceMeta:
    base = dict(
        container="mp4", video_codec="h264", audio_codec="aac",
        width=1920, height=1080, duration=30.0, bitrate=5_000_000, fps=30.0,
        has_audio=True, video_streams=1, audio_streams=1,
    )
    base.update(kw)
    return SourceMeta(**base)


def test_golden_argv_720p(monkeypatch):
    # pin every flag for a canonical source — the regression guard against flag drift
    monkeypatch.setattr(config, "x264_preset", "veryfast")
    argv = build_rendition_argv(meta(), PRESETS["720p"], "in.mp4", "/o")
    assert argv == [
        "ffmpeg", "-nostdin", "-y", "-i", "in.mp4",
        "-vf", "scale=w=1280:h=720:force_original_aspect_ratio=decrease:force_divisible_by=2",
        "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "high",
        "-b:v", "2800k", "-maxrate", "2996k", "-bufsize", "4200k",
        "-g", "60", "-keyint_min", "60", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "128k",
        "-hls_time", "4", "-hls_playlist_type", "vod", "-hls_flags", "independent_segments",
        "-hls_segment_filename", "/o/seg_%05d.ts", "/o/index.m3u8",
    ]


def test_no_audio_omits_all_audio_flags():
    argv = build_rendition_argv(meta(has_audio=False, audio_codec=None), PRESETS["720p"], "in.mp4", "/o")
    assert "-c:a" not in argv and "-b:a" not in argv and "-an" not in argv


def test_audio_bitrate_is_per_preset():
    argv = build_rendition_argv(meta(), PRESETS["480p"], "in.mp4", "/o")
    i = argv.index("-b:a")
    assert argv[i + 1] == "96k"


@pytest.mark.parametrize("fps,g", [(24.0, "48"), (60.0, "120")])
def test_gop_flags_follow_probed_fps(fps, g):
    argv = build_rendition_argv(meta(fps=fps), PRESETS["1080p"], "in.mp4", "/o")
    assert argv[argv.index("-g") + 1] == g
    assert argv[argv.index("-keyint_min") + 1] == g


def test_scale_filter_preserves_aspect_and_forces_even():
    # portrait source: the filter must fit-inside (decrease) and round to even — never pad/stretch
    argv = build_rendition_argv(meta(width=720, height=1280), PRESETS["720p"], "in.mp4", "/o")
    vf = argv[argv.index("-vf") + 1]
    assert "force_original_aspect_ratio=decrease" in vf
    assert "force_divisible_by=2" in vf


def test_argv_is_a_list_never_a_shell_string():
    argv = build_rendition_argv(meta(), PRESETS["360p"], "weird name; rm -rf.mp4", "/o")
    assert isinstance(argv, list)
    assert "weird name; rm -rf.mp4" in argv     # untrusted path is a single argv element, not interpolated
