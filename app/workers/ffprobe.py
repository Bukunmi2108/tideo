import subprocess, json
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

@dataclass(frozen=True)
class SourceMeta:
    container: str
    video_codec: Optional[str]
    audio_codec: Optional[str]
    width: int
    height: int
    duration: float
    bitrate: Optional[int]
    fps: Optional[float]
    has_audio: bool
    video_streams: int
    audio_streams: int

    @classmethod
    def from_ffprobe(cls, probe_data: Dict[str, Any]) -> "SourceMeta":
        """Parses ffprobe JSON output into a rotation-normalized SourceMeta instance."""
        format_info = probe_data.get("format", {})
        streams = probe_data.get("streams", [])

        v_streams = [s for s in streams if s.get("codec_type") == "video"]
        a_streams = [s for s in streams if s.get("codec_type") == "audio"]

        if not v_streams:
            raise InspectError(SOURCE_NO_VIDEO, "no video stream")

        first_v = v_streams[0] if v_streams else {}
        first_a = a_streams[0] if a_streams else {}

        width = int(first_v.get("width", 0))
        height = int(first_v.get("height", 0))
        
        rotation = 0
        for side_data in first_v.get("side_data_list", []):
            if "rotation" in side_data:
                rotation = abs(int(side_data["rotation"]))
                break
        if not rotation:
            rotation = abs(int(first_v.get("tags", {}).get("rotate", 0)))

        if rotation in (90, 270):
            width, height = height, width

        fps: Optional[float] = None
        fps_raw = first_v.get("r_frame_rate")
        if fps_raw and "/" in fps_raw:
            try:
                num, den = map(int, fps_raw.split("/"))
                if den != 0:
                    fps = num / den
            except (ValueError, ZeroDivisionError):
                fps = None

        bitrate_raw = format_info.get("bit_rate")
        bitrate = int(bitrate_raw) if bitrate_raw is not None else None

        return cls(
            container=format_info.get("format_name", ""),
            video_codec=first_v.get("codec_name") if v_streams else None,
            audio_codec=first_a.get("codec_name") if a_streams else None,
            width=width,
            height=height,
            duration=float(format_info.get("duration", 0.0)),
            bitrate=bitrate,
            fps=fps,
            has_audio=len(a_streams) > 0,
            video_streams=len(v_streams),
            audio_streams=len(a_streams),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Returns a JSON-serializable dictionary for Celery backend storage."""
        return asdict(self)

SOURCE_CORRUPT = "SOURCE_CORRUPT"
SOURCE_NO_VIDEO = "SOURCE_NO_VIDEO"

class InspectError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")

def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""

def run_ffprobe(path: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True,
    )

def probe(path: str) -> SourceMeta:
    result = run_ffprobe(path)
    if result.returncode != 0:
        raise InspectError(SOURCE_CORRUPT, _last_line(result.stderr) or "ffprobe failed")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise InspectError(SOURCE_CORRUPT, "unparseable ffprobe output")
    return SourceMeta.from_ffprobe(data) 