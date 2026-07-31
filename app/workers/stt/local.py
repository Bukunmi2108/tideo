from collections.abc import Callable

from app.core.config import config
from app.domain.errors import STT_UNAVAILABLE, TRANSCRIBE, make_error
from app.domain.vtt import Segment
from app.workers.stt.base import SttCancelled, SttUpstreamError

_model = None


def _load_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(config.stt_model, device="cpu", compute_type=config.stt_compute_type)
    return _model


class LocalProvider:
    """Local faster-whisper provider."""

    def transcribe(self, wav_path: str, cancelled: Callable[[], bool] | None = None) -> list[Segment]:
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
        except Exception as e:  # noqa: BLE001
            raise SttUpstreamError(make_error(STT_UNAVAILABLE, f"local stt failed: {e}", TRANSCRIBE))
