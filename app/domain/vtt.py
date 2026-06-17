from dataclasses import dataclass

WRAP_WIDTH = 42


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


def _timestamp(seconds: float) -> str:
    ms = max(0, round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def _wrap(text: str, width: int = WRAP_WIDTH) -> str:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        if cur and len(cur) + 1 + len(word) > width:
            lines.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def render_vtt(segments: list[Segment]) -> str:
    """WebVTT from transcript segments. Pure. Skips blank cues; an empty list yields a valid header-only file."""
    out = ["WEBVTT", ""]
    cue = 0
    for seg in segments:
        text = _wrap(seg.text.strip())
        if not text:
            continue
        cue += 1
        out += [str(cue), f"{_timestamp(seg.start)} --> {_timestamp(seg.end)}", text, ""]
    return "\n".join(out) + "\n"
