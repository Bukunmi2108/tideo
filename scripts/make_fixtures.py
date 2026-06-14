"""Generate the fixture suite from FFmpeg's synthetic sources.

Run inside the worker image so it uses the pinned FFmpeg:
    docker compose run --rm --no-deps celery python scripts/make_fixtures.py
"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures_spec import FIXTURES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"
FORCE = os.environ.get("FORCE", "").lower() in {"1", "true", "yes"}


def _tmp_path(name: str) -> Path:
    p = Path(name)
    return FIXTURES_DIR / f"{p.stem}.tmp{p.suffix}"


def _run(argv: list[str]) -> None:
    subprocess.run(argv, check=True)


def build_ffmpeg(spec: dict, out: Path) -> None:
    _run(["ffmpeg", "-y", "-loglevel", "error", *spec["inputs"], *spec["args"], str(out)])


def build_speech(spec: dict, out: Path) -> None:
    speech = FIXTURES_DIR / ".speech.tmp.wav"
    _run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"flite=text='{spec['text']}':voice={spec['voice']}",
        str(speech),
    ])
    try:
        _run([
            "ffmpeg", "-y", "-loglevel", "error",
            *spec["video_input"],
            "-stream_loop", "-1", "-i", str(speech),
            *spec["args"], str(out),
        ])
    finally:
        speech.unlink(missing_ok=True)


def build_truncate(spec: dict, out: Path) -> None:
    full = FIXTURES_DIR / ".corrupt_full.tmp.mp4"
    _run(["ffmpeg", "-y", "-loglevel", "error", *spec["inputs"], *spec["args"], str(full)])
    try:
        data = full.read_bytes()
        out.write_bytes(data[: int(len(data) * spec["keep"])])
    finally:
        full.unlink(missing_ok=True)


BUILDERS = {
    "ffmpeg": build_ffmpeg,
    "speech": build_speech,
    "truncate": build_truncate,
}


def main() -> None:
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    for spec in FIXTURES:
        name = spec["name"]
        final = FIXTURES_DIR / name
        if final.exists() and not FORCE:
            print(f"skip   {name} (exists)")
            continue
        tmp = _tmp_path(name)
        print(f"build  {name} ...")
        try:
            BUILDERS[spec["kind"]](spec, tmp)
            os.replace(tmp, final)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        print(f"  ok   {name}")
    print("fixtures complete")


if __name__ == "__main__":
    main()
