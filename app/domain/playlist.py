import math
from dataclasses import dataclass

_PROFILE_IDC = {"Baseline": "42", "Constrained Baseline": "42", "Main": "4d", "High": "64"}

def avc1_codec(profile: str, level: int) -> str:
    idc = _PROFILE_IDC.get(profile, "4d")
    return f"avc1.{idc}00{level:02x}"

def bandwidths(segments: list[tuple[int, float]]) -> tuple[int, int]:
    """Return the peak segment and whole-rendition bitrates required by HLS."""
    if not segments:
        raise ValueError("rendition has no media segments")
    if any(size < 0 or not math.isfinite(duration) or duration <= 0
           for size, duration in segments):
        raise ValueError("rendition has an invalid segment measurement")
    peak = max(math.ceil(size * 8 / duration) for size, duration in segments)
    average = int(sum(size for size, _ in segments) * 8
                  / sum(duration for _, duration in segments))
    return peak, average

@dataclass(frozen=True)
class Variant:
    preset: str
    bandwidth: int
    width: int
    height: int
    codecs: str
    average_bandwidth: int | None = None

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
        average = (f",AVERAGE-BANDWIDTH={v.average_bandwidth}"
                   if v.average_bandwidth is not None else "")
        lines.append(
            f'#EXT-X-STREAM-INF:BANDWIDTH={v.bandwidth}{average},'
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
                   *, web_remuxed: bool, created_at: str | None,
                   media_start_time: float,
                   storyboard: dict | None = None) -> dict:
    """Build the public manifest; late subtitle completion also reads its rendition metadata."""
    return {
        "job_id": job_id,
        "duration": duration,
        "renditions": [
            {
                "preset": v.preset,
                "bandwidth": v.bandwidth,
                **({"average_bandwidth": v.average_bandwidth}
                   if v.average_bandwidth is not None else {}),
                "resolution": f"{v.width}x{v.height}",
                "codecs": v.codecs,
            }
            for v in variants
        ],
        "master": "master.m3u8",
        "web_mp4": "web.mp4",
        "web_remuxed": web_remuxed,
        "poster": "poster.jpg",
        "sprite": "sprite.jpg",
        "storyboard": storyboard,
        "created_at": created_at,
        "media_start_time": media_start_time,
    }
