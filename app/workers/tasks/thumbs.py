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

def _image_size(path: str) -> tuple[int, int]:
    s = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height",
                        "-of", "json", path], capture_output=True, text=True)
    streams = json.loads(s.stdout or "{}").get("streams", [])
    if not streams or int(streams[0].get("width", 0)) == 0:
        raise RuntimeError(f"invalid image: {path}")
    return int(streams[0]["width"]), int(streams[0]["height"])


def write_poster(out_dir: Path, src: str, duration: float) -> None:
    with paths.atomic_path(out_dir / "poster.jpg") as tmp:
        subprocess.run(_poster_argv(src, duration, str(tmp)), check=True)
        _image_size(str(tmp))


def write_sprite(out_dir: Path, src: str, duration: float, fps: float) -> dict:
    """Build the scrub sprite and return its storyboard geometry — enough for a client to map a
    timestamp to a tile (background-position) for hover-scrub previews. tile size is read back from the
    produced sheet so it stays correct across source aspect ratios."""
    plan = sprite_plan(duration, fps or 30.0)
    with paths.atomic_path(out_dir / "sprite.jpg") as tmp:
        subprocess.run(_sprite_argv(src, plan, str(tmp)), check=True)
        sheet_w, sheet_h = _image_size(str(tmp))
    return {
        "url": "sprite.jpg",
        "tiles": plan.tiles,
        "cols": plan.cols,
        "rows": plan.rows,
        "tile_w": sheet_w // plan.cols,
        "tile_h": sheet_h // plan.rows,
        "interval": round(duration / plan.tiles, 4) if plan.tiles else 0,
    }