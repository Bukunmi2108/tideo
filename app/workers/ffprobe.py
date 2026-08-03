import json
import math
import subprocess
from dataclasses import dataclass
from typing import Any

from app.domain.errors import (
    INSPECTION_UNAVAILABLE,
    SOURCE_CORRUPT,
    SOURCE_NO_VIDEO,
)


class InspectError(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class SourceMeta:
    container: str
    video_codec: str | None
    audio_codec: str | None
    width: int
    height: int
    duration: float
    bitrate: int | None
    fps: float | None
    has_audio: bool
    video_streams: int
    audio_streams: int

    @classmethod
    def from_ffprobe(cls, probe_data: dict[str, Any]) -> "SourceMeta":
        try:
            format_info = probe_data.get("format", {})
            streams = probe_data.get("streams", [])
            if not isinstance(format_info, dict) or not isinstance(streams, list):
                raise TypeError

            v_streams = [
                stream
                for stream in streams
                if isinstance(stream, dict) and stream.get("codec_type") == "video"
            ]
            a_streams = [
                stream
                for stream in streams
                if isinstance(stream, dict) and stream.get("codec_type") == "audio"
            ]
            if not v_streams:
                raise InspectError(SOURCE_NO_VIDEO, "no video stream")

            first_v = v_streams[0]
            first_a = a_streams[0] if a_streams else {}
            width = int(first_v.get("width", 0))
            height = int(first_v.get("height", 0))
            duration = float(format_info.get("duration", 0.0))
            bitrate_raw = format_info.get("bit_rate")
            bitrate = int(bitrate_raw) if bitrate_raw is not None else None

            rotation = 0
            for side_data in first_v.get("side_data_list", []):
                if isinstance(side_data, dict) and "rotation" in side_data:
                    rotation = abs(int(side_data["rotation"]))
                    break
            if not rotation:
                rotation = abs(int(first_v.get("tags", {}).get("rotate", 0)))
            if rotation in (90, 270):
                width, height = height, width
        except InspectError:
            raise
        except (AttributeError, OverflowError, TypeError, ValueError) as exc:
            raise InspectError(SOURCE_CORRUPT, "invalid media metadata") from exc

        if (
            width <= 0
            or height <= 0
            or not math.isfinite(duration)
            or duration <= 0
            or (bitrate is not None and bitrate < 0)
        ):
            raise InspectError(SOURCE_CORRUPT, "invalid media metadata")

        fps = None
        fps_raw = first_v.get("r_frame_rate")
        if isinstance(fps_raw, str) and "/" in fps_raw:
            try:
                numerator, denominator = map(int, fps_raw.split("/"))
                if numerator > 0 and denominator > 0:
                    candidate = numerator / denominator
                    fps = candidate if math.isfinite(candidate) else None
            except (OverflowError, TypeError, ValueError):
                pass

        return cls(
            container=str(format_info.get("format_name") or ""),
            video_codec=first_v.get("codec_name"),
            audio_codec=first_a.get("codec_name"),
            width=width,
            height=height,
            duration=duration,
            bitrate=bitrate,
            fps=fps,
            has_audio=bool(a_streams),
            video_streams=len(v_streams),
            audio_streams=len(a_streams),
        )


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def run_ffprobe(path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            path,
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )


def probe(path: str) -> SourceMeta:
    try:
        result = run_ffprobe(path)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise InspectError(INSPECTION_UNAVAILABLE, "ffprobe unavailable") from exc
    if result.returncode != 0:
        raise InspectError(SOURCE_CORRUPT, _last_line(result.stderr) or "ffprobe failed")
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise InspectError(SOURCE_CORRUPT, "unparseable ffprobe output") from exc
    return SourceMeta.from_ffprobe(data)
