from app.core.config import config
from app.domain.ladder import Preset, gop_size
from app.workers.ffprobe import SourceMeta

def build_rendition_argv(meta: SourceMeta, preset: Preset, src: str, out_dir: str) -> list[str]:
    """Pure: (SourceMeta, Preset, paths) -> ffmpeg argv. No I/O. argv-list, never a shell string."""
    g = gop_size(meta.fps or 30.0)   # fps is set for any real video; 30 is a safe fallback
    vf = (f"scale=w={preset.width}:h={preset.height}"
          f":force_original_aspect_ratio=decrease:force_divisible_by=2")
    argv = [
        "ffmpeg", "-nostdin", "-y", "-i", src,
        "-vf", vf,
        "-c:v", "libx264", "-preset", config.x264_preset, "-profile:v", preset.profile,
        "-b:v", preset.v_bitrate, "-maxrate", preset.maxrate, "-bufsize", preset.bufsize,
        "-g", str(g), "-keyint_min", str(g), "-sc_threshold", "0",
    ]
    if meta.has_audio:
        argv += ["-c:a", "aac", "-b:a", preset.a_bitrate]   # omit ALL audio flags when no audio stream
    argv += [
        "-hls_time", "4", "-hls_playlist_type", "vod", "-hls_flags", "independent_segments",
        "-hls_segment_filename", f"{out_dir}/seg_%05d.ts", f"{out_dir}/index.m3u8",
    ]
    return argv