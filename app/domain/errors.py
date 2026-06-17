import re
from dataclasses import dataclass

INSPECT = "inspect"
TRANSCODE = "transcode"
PACKAGE = "package"

SOURCE_CORRUPT = "SOURCE_CORRUPT"
SOURCE_UNSUPPORTED = "SOURCE_UNSUPPORTED"
SOURCE_NO_VIDEO = "SOURCE_NO_VIDEO"
SOURCE_LIMITS_EXCEEDED = "SOURCE_LIMITS_EXCEEDED"
ENCODE_FAILED_TRANSIENT = "ENCODE_FAILED_TRANSIENT"
ENCODE_TIMEOUT = "ENCODE_TIMEOUT"
STORAGE_FULL = "STORAGE_FULL"
CANCELLED = "CANCELLED"

_RETRYABLE = {
    SOURCE_CORRUPT: False,
    SOURCE_UNSUPPORTED: False,
    SOURCE_NO_VIDEO: False,
    SOURCE_LIMITS_EXCEEDED: False,
    ENCODE_FAILED_TRANSIENT: True,
    ENCODE_TIMEOUT: True,
    STORAGE_FULL: False,
    CANCELLED: False,
}


def is_retryable(code: str) -> bool:
    return _RETRYABLE.get(code, True)  # unknown -> transient: a wasted retry beats a wrongly-permanent fail


@dataclass(frozen=True)
class TideoError:
    code: str
    message: str
    stage: str
    retryable: bool


def make_error(code: str, message: str, stage: str) -> TideoError:
    return TideoError(code, message, stage, is_retryable(code))


def _last_line(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


# first match wins — keep corrupt patterns ahead of the broader codec ones
_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"moov atom not found|invalid data found|truncat", re.I), SOURCE_CORRUPT),
    (re.compile(r"decoder.*not found|unknown decoder|unknown encoder|no such filter", re.I), SOURCE_UNSUPPORTED),
    (re.compile(r"no space left on device|enospc", re.I), STORAGE_FULL),
]


def classify(exit_code: int | None, stderr_tail: str, *, stage: str = TRANSCODE) -> TideoError:
    """Map a process failure to a TideoError. stderr patterns first, then signal-death,
    then an unclassified transient default (the caller logs the full stderr to grow the corpus)."""
    text = stderr_tail or ""
    for pat, code in _PATTERNS:
        if pat.search(text):
            return make_error(code, _last_line(text) or code, stage)
    if exit_code is not None and (exit_code < 0 or exit_code in (137, 143)):  # SIGKILL/OOM, SIGTERM
        return make_error(ENCODE_FAILED_TRANSIENT, f"process killed (exit {exit_code})", stage)
    return make_error(ENCODE_FAILED_TRANSIENT, _last_line(text) or f"encode failed (exit {exit_code})", stage)
