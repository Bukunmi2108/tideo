from pathlib import Path

from app.domain.ladder import PRESETS
from app.domain.playlist import Variant, avc1_codec, bandwidth, build_manifest, build_master

REFERENCE = Path(__file__).resolve().parent.parent / "fixtures" / "reference" / "master.m3u8"


# ---------- avc1_codec (both ladder profiles) ----------

def test_avc1_high_profile():
    assert avc1_codec("High", 31) == "avc1.64001f"


def test_avc1_main_profile():
    assert avc1_codec("Main", 30) == "avc1.4d001e"


def test_avc1_unknown_profile_defaults_main():
    assert avc1_codec("Potato", 30).startswith("avc1.4d")


# ---------- bandwidth: measured, not configured ----------

def test_bandwidth_is_measured_not_configured_target():
    # a complex 720p source that landed well under its 2800k target
    bw = bandwidth(output_bytes=6_000_000, duration=30.0)     # ~1.6 Mbps actual
    configured = int(PRESETS["720p"].v_bitrate.rstrip("k")) * 1000
    assert bw != configured                                   # provably derived from real bytes
    assert bw == int(6_000_000 * 8 / 30.0 * 1.1)


def test_bandwidth_zero_duration_is_zero():
    assert bandwidth(1_000_000, 0) == 0


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


# ---------- manifest schema ----------

def test_manifest_schema():
    v = [Variant("720p", 2156000, 1280, 720, "avc1.64001f,mp4a.40.2")]
    m = build_manifest("j1", 30.0, v, web_remuxed=True, created_at="2026-06-16T00:00:00+00:00")
    assert set(m) == {"job_id", "duration", "renditions", "master", "web_mp4",
                      "web_remuxed", "created_at"}
    assert m["master"] == "master.m3u8" and m["web_mp4"] == "web.mp4"
    assert m["renditions"][0] == {
        "preset": "720p", "bandwidth": 2156000,
        "resolution": "1280x720", "codecs": "avc1.64001f,mp4a.40.2",
    }
