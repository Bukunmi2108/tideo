import math
from dataclasses import dataclass
import json, subprocess
from pathlib import Path
from app.storage import paths


@dataclass(frozen=True)
class SpritePlan:
    tiles: int
    cols: int
    rows: int
    sample_fps: str

def sprite_plan(duration: float, fps: float, target: int = 100, max_cols: int = 10) -> SpritePlan:
    total_frames = max(1, int(duration * (fps or 30.0)))
    tiles = min(target, total_frames)
    cols = min(max_cols, tiles)
    rows = math.ceil(tiles / cols)
    return SpritePlan(tiles, cols, rows, f"{tiles}/{duration}")

def _poster_argv(src, duration, out):
    ts = max(0.0, duration * 0.1)
    return ["ffmpeg", "-nostdin", "-y", "-ss", f"{ts:.3f}", "-i", src,
            "-frames:v", "1", "-vf", "scale=1280:-2", "-q:v", "3", out]

def _sprite_argv(src, plan: SpritePlan, out):
    vf = f"fps={plan.sample_fps},scale=160:-2,tile={plan.cols}x{plan.rows}"
    return ["ffmpeg", "-nostdin", "-y", "-i", src, "-vf", vf, "-frames:v", "1", out]

def _verify_image(path: str) -> None:
    s = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                        "-of", "json", path], capture_output=True, text=True)
    streams = json.loads(s.stdout or "{}").get("streams", [])
    if not streams or int(streams[0].get("width", 0)) == 0:
        raise RuntimeError(f"invalid image: {path}")

def write_poster(out_dir: Path, src: str, duration: float) -> None:
    with paths.atomic_path(out_dir / "poster.jpg") as tmp:
        subprocess.run(_poster_argv(src, duration, str(tmp)), check=True)
        _verify_image(str(tmp))


def write_sprite(out_dir: Path, src: str, duration: float, fps: float) -> int:
    plan = sprite_plan(duration, fps or 30.0)
    with paths.atomic_path(out_dir / "sprite.jpg") as tmp:
        subprocess.run(_sprite_argv(src, plan, str(tmp)), check=True)
        _verify_image(str(tmp))
    return plan.tiles