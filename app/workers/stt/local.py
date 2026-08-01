from collections.abc import Callable

from app.core.config import config
from app.domain.errors import STT_UNAVAILABLE, TRANSCRIBE, TideoError, make_error
from app.domain.vtt import Segment

_model = None


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
    except SttCancelled:
        raise
    except Exception as exc:
        error = make_error(STT_UNAVAILABLE, f"local stt failed: {exc}", TRANSCRIBE)
        raise SttError(error) from exc
