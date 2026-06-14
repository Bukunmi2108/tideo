"""Self-check the fixture suite: ffprobe each fixture and assert the spec's expectations.

    docker compose run --rm --no-deps celery python scripts/verify_fixtures.py
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fixtures_spec import FIXTURES  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT / "fixtures"
DURATION_TOLERANCE = 1.0


def probe(path: Path) -> tuple[int, dict]:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    try:
        data = json.loads(r.stdout) if r.stdout else {}
    except json.JSONDecodeError:
        data = {}
    return r.returncode, data


def check(spec: dict) -> list[str]:
    name = spec["name"]
    path = FIXTURES_DIR / name
    expect = spec["expect"]
    if not path.exists():
        return [f"missing file"]

    rc, data = probe(path)
    streams = data.get("streams", [])
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]

    if expect.get("unreadable"):
        if rc == 0 and videos:
            return ["expected unreadable / no video stream, but a video stream was found"]
        return []

    fails: list[str] = []
    if rc != 0:
        return [f"ffprobe failed (rc={rc})"]

    if expect["video"] is None:
        if videos:
            fails.append(f"expected no video stream, found {videos[0].get('codec_name')}")
    elif not videos:
        fails.append("no video stream")
    else:
        v = videos[0]
        if v.get("codec_name") != expect["video"]:
            fails.append(f"video codec {v.get('codec_name')} != {expect['video']}")
        if (v.get("width"), v.get("height")) != (expect["width"], expect["height"]):
            fails.append(f"dims {v.get('width')}x{v.get('height')} != {expect['width']}x{expect['height']}")

    if expect["audio"] is None:
        if audios:
            fails.append(f"expected no audio, found {len(audios)}")
    elif not audios:
        fails.append(f"expected audio {expect['audio']}, found none")
    elif audios[0].get("codec_name") != expect["audio"]:
        fails.append(f"audio codec {audios[0].get('codec_name')} != {expect['audio']}")

    dur = float(data.get("format", {}).get("duration") or 0)
    if abs(dur - expect["duration"]) > DURATION_TOLERANCE:
        fails.append(f"duration {dur:.2f}s != {expect['duration']}s (+/-{DURATION_TOLERANCE})")

    return fails


def main() -> None:
    total = 0
    for spec in FIXTURES:
        fails = check(spec)
        print(f"[{'OK' if not fails else 'FAIL'}] {spec['name']}")
        for f in fails:
            print(f"        {f}")
        total += len(fails)
    if total:
        print(f"\n{total} problem(s) found")
        sys.exit(1)
    print("\nall fixtures verified")


if __name__ == "__main__":
    main()
