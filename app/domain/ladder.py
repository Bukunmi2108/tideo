from dataclasses import dataclass

@dataclass(frozen=True)
class Preset:
    name: str
    width: int
    height: int
    v_bitrate: str
    maxrate: str
    bufsize: str
    a_bitrate: str
    profile: str        # x264 profile: "high" | "main"

PRESETS: dict[str, Preset] = {
    "1080p": Preset("1080p", 1920, 1080, "5000k", "5350k", "7500k", "128k", "high"),
    "720p":  Preset("720p",  1280,  720, "2800k", "2996k", "4200k", "128k", "high"),
    "480p":  Preset("480p",   854,  480, "1400k", "1498k", "2100k",  "96k", "main"),
    "360p":  Preset("360p",   640,  360,  "800k",  "856k", "1200k",  "96k", "main"),
}

def gop_size(fps: float) -> int:
    """GOP length in frames for 4s segments = 2 GOPs, derived from the PROBED fps."""
    return round(fps) * 2