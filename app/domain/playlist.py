from dataclasses import dataclass

# H.264 profile name -> profile_idc hex. CODECS = avc1.<idc><constraints><level-hex>.
_PROFILE_IDC = {"Baseline": "42", "Constrained Baseline": "42", "Main": "4d", "High": "64"}

def avc1_codec(profile: str, level: int) -> str:
    idc = _PROFILE_IDC.get(profile, "4d")            # default Main if unknown
    return f"avc1.{idc}00{level:02x}"                # e.g. High, level 31 -> avc1.64001f

def bandwidth(output_bytes: int, duration: float) -> int:
    """Measured bitrate (bits/s) from actual output, padded ~10% for peak. NOT the configured target."""
    if not duration:
        return 0
    return int(output_bytes * 8 / duration * 1.1)

@dataclass(frozen=True)
class Variant:
    preset: str
    bandwidth: int
    width: int
    height: int
    codecs: str

SUBS_GROUP = "subs"


def build_master(variants: list[Variant], *, has_subtitles: bool = False) -> str:
    lines = ["#EXTM3U", "#EXT-X-VERSION:6"]
    if has_subtitles:
        lines.append(
            f'#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="{SUBS_GROUP}",NAME="English",'
            f'DEFAULT=YES,AUTOSELECT=YES,LANGUAGE="en",URI="playlist/subs"'
        )
    subs_attr = f',SUBTITLES="{SUBS_GROUP}"' if has_subtitles else ""
    for v in sorted(variants, key=lambda x: x.bandwidth, reverse=True):
        lines.append(
            f'#EXT-X-STREAM-INF:BANDWIDTH={v.bandwidth},'
            f'RESOLUTION={v.width}x{v.height},CODECS="{v.codecs}"{subs_attr}'
        )
        lines.append(f"playlist/{v.preset}")
    return "\n".join(lines) + "\n"


def build_subtitle_media_playlist(duration: float) -> str:
    """A one-cue VOD media playlist wrapping subtitles.vtt — some players reject a bare VTT URI.
    Served at /jobs/{id}/playlist/subs; the `../subtitles` ref resolves to /jobs/{id}/subtitles."""
    target = max(1, int(duration) + 1)
    return "\n".join([
        "#EXTM3U",
        "#EXT-X-VERSION:6",
        f"#EXT-X-TARGETDURATION:{target}",
        "#EXT-X-MEDIA-SEQUENCE:0",
        "#EXT-X-PLAYLIST-TYPE:VOD",
        f"#EXTINF:{duration:.3f},",
        "../subtitles",
        "#EXT-X-ENDLIST",
    ]) + "\n"

def build_manifest(job_id: str, duration: float, variants: list[Variant],
                   *, web_remuxed: bool, created_at: str | None, storyboard: dict | None = None) -> dict:
    """Machine-readable result summary. Schema is a contract: the API `done` response, the frontend,
    and dedupe's lazy-verify all read it. Pure -> assertable."""
    return {
        "job_id": job_id,
        "duration": duration,
        "renditions": [{"preset": v.preset, "bandwidth": v.bandwidth,
                        "resolution": f"{v.width}x{v.height}", "codecs": v.codecs} for v in variants],
        "master": "master.m3u8",
        "web_mp4": "web.mp4",
        "web_remuxed": web_remuxed,
        "poster": "poster.jpg",
        "sprite": "sprite.jpg",
        "storyboard": storyboard,
        "created_at": created_at,
    }