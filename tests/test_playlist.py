from pathlib import Path

import pytest

from app.domain.ladder import PRESETS
from app.domain.playlist import (
    Variant,
    avc1_codec,
    bandwidths,
    build_manifest,
    build_master,
)

REFERENCE = Path(__file__).resolve().parent.parent / "fixtures" / "reference" / "master.m3u8"


# ---------- avc1_codec (both ladder profiles) ----------

def test_avc1_high_profile():
    assert avc1_codec("High", 31) == "avc1.64001f"


def test_avc1_main_profile():
    assert avc1_codec("Main", 30) == "avc1.4d001e"


def test_avc1_unknown_profile_defaults_main():
    assert avc1_codec("Potato", 30).startswith("avc1.4d")


# ---------- bandwidth: measured per segment ----------

def test_bandwidths_report_peak_segment_and_true_average():
    peak, average = bandwidths([
        (500_000, 4.0),
        (900_000, 4.0),
        (100_000, 1.0),
    ])

    assert peak == 1_800_000
    assert average == int(1_500_000 * 8 / 9.0)
    configured = int(PRESETS["720p"].v_bitrate.rstrip("k")) * 1000
    assert peak != configured


@pytest.mark.parametrize("segments", [[], [(100, 0)], [(100, -1)], [(-1, 1)]])
def test_bandwidths_reject_invalid_measurements(segments):
    with pytest.raises(ValueError):
        bandwidths(segments)


# ---------- build_master vs the golden reference ----------

def test_build_master_matches_reference():
    variants = [
        Variant("1080p", 3850000, 1920, 1080, "avc1.64001f,mp4a.40.2"),
        Variant("720p",  2156000, 1280,  720, "avc1.64001f,mp4a.40.2"),
        Variant("480p",  1078000,  854,  480, "avc1.4d001e,mp4a.40.2"),
    ]
    assert build_master(variants) == REFERENCE.read_text()


def test_build_master_orders_highest_bandwidth_first():
    variants = [
        Variant("480p", 1000000, 854, 480, "avc1.4d001e,mp4a.40.2"),
        Variant("1080p", 5000000, 1920, 1080, "avc1.64001f,mp4a.40.2"),
    ]
    out = build_master(variants)
    assert out.index("1920x1080") < out.index("854x480")      # top rung listed first


def test_build_master_uses_actual_resolution_for_portrait():
    out = build_master([Variant("720p", 1500000, 404, 720, "avc1.64001f,mp4a.40.2")])
    assert "RESOLUTION=404x720" in out                        # portrait stays portrait


def test_build_master_emits_peak_and_average_bandwidth():
    out = build_master([
        Variant("360p", 1_120_000, 640, 360, "avc1.4d001e,mp4a.40.2", 840_000),
    ])
    assert "BANDWIDTH=1120000,AVERAGE-BANDWIDTH=840000" in out


# ---------- manifest schema ----------

def test_manifest_schema():
    v = [Variant("720p", 2156000, 1280, 720, "avc1.64001f,mp4a.40.2")]
    m = build_manifest("j1", 30.0, v, web_remuxed=True, created_at="2026-06-16T00:00:00+00:00",
                       media_start_time=1.4,
                       storyboard={"url": "sprite.jpg", "tiles": 100, "cols": 10, "rows": 10,
                                   "tile_w": 160, "tile_h": 90, "interval": 0.3})
    assert set(m) == {"job_id", "duration", "renditions", "master", "web_mp4",
                      "web_remuxed", "poster", "sprite", "storyboard", "created_at",
                      "media_start_time"}
    assert m["storyboard"]["cols"] == 10
    assert m["master"] == "master.m3u8" and m["web_mp4"] == "web.mp4"
    assert m["media_start_time"] == 1.4
    assert m["renditions"][0] == {
        "preset": "720p", "bandwidth": 2156000,
        "resolution": "1280x720", "codecs": "avc1.64001f,mp4a.40.2",
    }
