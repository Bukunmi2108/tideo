import pytest

from app.domain.vtt import Segment, _timestamp, _wrap, render_vtt, with_timestamp_map


def test_timestamp_formats_hms_millis():
    assert _timestamp(0) == "00:00:00.000"
    assert _timestamp(1.5) == "00:00:01.500"
    assert _timestamp(3661.25) == "01:01:01.250"
    assert _timestamp(-1) == "00:00:00.000"          # clamp, never a negative cue time


def test_wrap_breaks_on_width_without_splitting_words():
    out = _wrap("the quick brown fox jumps over the lazy dog again", width=20)
    assert all(len(line) <= 20 for line in out.split("\n"))
    assert out.replace("\n", " ") == "the quick brown fox jumps over the lazy dog again"


def test_render_numbers_cues_and_formats_timing():
    vtt = render_vtt([Segment(0, 1.5, "hello"), Segment(1.5, 3, "world")])
    assert vtt.startswith("WEBVTT\n\n")
    assert "1\n00:00:00.000 --> 00:00:01.500\nhello" in vtt
    assert "2\n00:00:01.500 --> 00:00:03.000\nworld" in vtt


def test_render_skips_blank_segments_and_renumbers():
    vtt = render_vtt([Segment(0, 1, "  "), Segment(1, 2, "real")])
    assert "1\n00:00:01.000 --> 00:00:02.000\nreal" in vtt
    assert "2\n" not in vtt                            # the blank cue produced nothing


def test_render_empty_is_valid_header_only():
    assert render_vtt([]) == "WEBVTT\n\n"


def test_timestamp_map_uses_the_mpeg_90khz_clock():
    mapped = with_timestamp_map("WEBVTT\n\n1\n00:00:00.000 --> 00:00:01.000\nhello\n", 1.4)
    assert mapped.startswith(
        "WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000,MPEGTS:126000\n\n"
    )
    assert mapped.endswith("00:00:00.000 --> 00:00:01.000\nhello\n")


def test_timestamp_map_is_idempotent_and_wraps_at_33_bits():
    source = "WEBVTT\n\n"
    once = with_timestamp_map(source, ((1 << 33) + 90_000) / 90_000)
    twice = with_timestamp_map(once, ((1 << 33) + 90_000) / 90_000)
    assert once == twice
    assert "MPEGTS:90000" in once


@pytest.mark.parametrize("seconds", [float("nan"), float("inf")])
def test_timestamp_map_rejects_non_finite_start_time(seconds):
    with pytest.raises(ValueError):
        with_timestamp_map("WEBVTT\n\n", seconds)
