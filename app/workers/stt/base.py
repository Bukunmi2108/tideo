from collections.abc import Callable
from typing import Protocol

from app.domain.errors import TideoError
from app.domain.vtt import Segment


class SttUpstreamError(Exception):
    """Classified STT failure."""

    def __init__(self, error: TideoError, retry_after: float | None = None):
        super().__init__(error.message)
        self.error = error
        self.retry_after = retry_after


class SttCancelled(Exception):
    pass


class SttProvider(Protocol):
    def transcribe(
        self,
        wav_path: str,
        cancelled: Callable[[], bool] | None = None,
    ) -> list[Segment]: ...
