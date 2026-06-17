from typing import Protocol

from app.domain.errors import TideoError
from app.domain.vtt import Segment


class SttUpstreamError(Exception):
    """Carries a classified TideoError (one taxonomy, two transports) plus an optional honored
    Retry-After. The transcribe task maps it to retry-or-fail-soft."""

    def __init__(self, error: TideoError, retry_after: float | None = None):
        super().__init__(error.message)
        self.error = error
        self.retry_after = retry_after


class SttProvider(Protocol):
    def transcribe(self, wav_path: str) -> list[Segment]: ...
