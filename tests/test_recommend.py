import pytest

from app.domain import recommend
from app.workers.ffprobe import SOURCE_LIMITS_EXCEEDED, InspectError, SourceMeta


def meta(**kw) -> SourceMeta:
    base = dict(
        container="mov,mp4,m4a,3gp,3g2,mj2", video_codec="h264", audio_codec="aac",
        width=1920, height=1080, duration=30.0, bitrate=5_000_000, fps=30.0,
        has_audio=True, video_streams=1, audio_streams=1,
    )
    base.update(kw)
    return SourceMeta(**base)


# ---- recommended_presets ----

def test_full_ladder_at_or_above_1080():
    assert recommend.recommended_presets(1440) == ["1080p", "720p", "480p", "360p"]
    assert recommend.recommended_presets(1080) == ["1080p", "720p", "480p", "360p"]


def test_ladder_capped_to_source_height():
    assert recommend.recommended_presets(720) == ["720p", "480p", "360p"]
    assert recommend.recommended_presets(480) == ["480p", "360p"]


def test_ladder_never_empty():
    assert recommend.recommended_presets(200) == ["360p"]


# ---- web_safe ----

def test_web_safe_true_for_h264_aac_mp4():
    assert recommend.web_safe(meta()) == (True, None)


def test_web_safe_true_when_no_audio():
    ok, reason = recommend.web_safe(meta(has_audio=False, audio_codec=None))
    assert ok is True and reason is None


def test_web_safe_false_lists_every_reason():
    ok, reason = recommend.web_safe(meta(container="matroska,webm", video_codec="vp9", audio_codec="opus"))
    assert ok is False
    assert "vp9" in reason and "opus" in reason and "matroska" in reason


def test_web_safe_false_on_container_alone():
    ok, reason = recommend.web_safe(meta(container="matroska,webm"))
    assert ok is False and "container" in reason


# ---- check_caps ----

def test_caps_pass_for_normal_source():
    recommend.check_caps(meta(), 7200)   # must not raise


@pytest.mark.parametrize("kw", [
    {"width": 9000, "height": 5000},     # > 8K
    {"duration": 99999.0},               # > max_seconds
    {"bitrate": 999_000_000},            # > sanity ceiling
])
def test_caps_reject_absurd_metadata(kw):
    with pytest.raises(InspectError) as exc:
        recommend.check_caps(meta(**kw), 7200)
    assert exc.value.code == SOURCE_LIMITS_EXCEEDED
