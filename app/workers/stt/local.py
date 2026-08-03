from collections.abc import Callable

from celery.exceptions import SoftTimeLimitExceeded

from app.core.config import config
from app.core.logging import get_logger
from app.domain.errors import STT_UNAVAILABLE, TRANSCRIBE, TideoError, make_error
from app.domain.vtt import Segment
from app.workers.cancellation import CancellationUnavailable

_model = None
log = get_logger()


class SttError(Exception):
    def __init__(self, error: TideoError):
        super().__init__(error.message)
        self.error = error


class SttCancelled(Exception):
    pass


def _load_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(config.stt_model, device="cpu", compute_type=config.stt_compute_type)
    return _model


def transcribe(wav_path: str, cancelled: Callable[[], bool] | None = None) -> list[Segment]:
    try:
        model = _load_model()
        segments, _info = model.transcribe(wav_path)
        out = []
        for segment in segments:
            if cancelled and cancelled():
                raise SttCancelled()
            out.append(Segment(segment.start, segment.end, segment.text))
        return out
    except (SttCancelled, CancellationUnavailable, SoftTimeLimitExceeded):
        raise
    except Exception as exc:
        log.exception("local_stt_failed")
        error = make_error(STT_UNAVAILABLE, "transcription service unavailable", TRANSCRIBE)
        raise SttError(error) from exc
