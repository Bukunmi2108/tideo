import random

from app.domain.errors import ENCODE_TIMEOUT, TideoError

RETRY_BASE_SECONDS = 5
RETRY_CAP_SECONDS = 120
MAX_RETRIES = 3
TIMEOUT_MAX_RETRIES = 1  # a stalled disk gets one more shot; a genuinely slow encode shouldn't burn three


def max_retries_for(err: TideoError) -> int:
    if not err.retryable:
        return 0
    return TIMEOUT_MAX_RETRIES if err.code == ENCODE_TIMEOUT else MAX_RETRIES


def backoff_seconds(attempt: int) -> float:
    """Full jitter: random(0, min(cap, base * 2^attempt)) — de-correlates simultaneous retries
    so N renditions failing on the same disk hiccup don't stampede it in lockstep."""
    ceiling = min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * (2 ** attempt))
    return random.uniform(0, ceiling)
