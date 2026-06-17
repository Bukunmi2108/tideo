import subprocess


def has_audio(meta: dict) -> bool:
    return bool(meta.get("audio_codec"))


def extract_audio(src: str, out_wav: str) -> None:
    """Mono 16 kHz WAV — the format every STT wants. Extracted once; the full video never goes upstream."""
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", src, "-vn", "-ac", "1", "-ar", "16000", out_wav],
        check=True, capture_output=True, text=True,
    )
