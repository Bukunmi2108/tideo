import pytest
from app.workers.ffprobe import SOURCE_NO_VIDEO, InspectError, SourceMeta


def _probe_json(streams, fmt=None):
    return {
        "format": fmt or {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "30.0", "bit_rate": "1000000"},
        "streams": streams,
    }


VIDEO = {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080, "r_frame_rate": "30/1"}
AUDIO = {"codec_type": "audio", "codec_name": "aac"}


def test_normal_source():
    meta = SourceMeta.from_ffprobe(_probe_json([VIDEO, AUDIO]))
    assert meta.video_codec == "h264"
    assert meta.audio_codec == "aac"
    assert (meta.width, meta.height) == (1920, 1080)
    assert meta.fps == 30.0
    assert meta.duration == 30.0
    assert meta.bitrate == 1_000_000
    assert meta.has_audio is True
    assert (meta.video_streams, meta.audio_streams) == (1, 1)


def test_no_audio():
    meta = SourceMeta.from_ffprobe(_probe_json([VIDEO]))
    assert meta.has_audio is False
    assert meta.audio_codec is None
    assert meta.audio_streams == 0


def test_rotation_swaps_display_dims():
    rotated = {**VIDEO, "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]}
    meta = SourceMeta.from_ffprobe(_probe_json([rotated]))
    assert (meta.width, meta.height) == (1080, 1920)  # coded 1920x1080 displayed portrait


def test_legacy_rotate_tag_swaps():
    rotated = {**VIDEO, "tags": {"rotate": "90"}}
    meta = SourceMeta.from_ffprobe(_probe_json([rotated]))
    assert (meta.width, meta.height) == (1080, 1920)


def test_no_rotation_keeps_dims():
    meta = SourceMeta.from_ffprobe(_probe_json([VIDEO]))
    assert (meta.width, meta.height) == (1920, 1080)


def test_no_video_raises_source_no_video():
    with pytest.raises(InspectError) as exc:
        SourceMeta.from_ffprobe(_probe_json([AUDIO]))  # audio only
    assert exc.value.code == SOURCE_NO_VIDEO


def test_missing_bitrate_tolerated():
    meta = SourceMeta.from_ffprobe(_probe_json([VIDEO], fmt={"format_name": "x", "duration": "5.0"}))
    assert meta.bitrate is None


def test_to_dict_is_json_serializable():
    import json
    meta = SourceMeta.from_ffprobe(_probe_json([VIDEO, AUDIO]))
    json.dumps(meta.to_dict())  # must not raise
