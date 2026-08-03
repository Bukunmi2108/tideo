from collections.abc import Callable

from app.workers.process import run_process


def has_audio(meta: dict) -> bool:
    return bool(meta.get("audio_codec"))


def extract_audio(
    src: str,
    out_wav: str,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> None:
    """Mono 16 kHz WAV — the format every STT wants. Extracted once; the full video never goes upstream."""
    run_process(
        ["ffmpeg", "-nostdin", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000", out_wav],
        check=True,
        capture_output=True,
        text=True,
        timeout=540,
        cancelled=cancelled,
    )
