from app.domain.vtt import Segment, render_vtt, _timestamp, _wrap


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
