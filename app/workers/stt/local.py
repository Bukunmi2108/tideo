from app.core.config import config
from app.domain.errors import STT_UNAVAILABLE, TRANSCRIBE, make_error
from app.domain.vtt import Segment
from app.workers.stt.base import SttUpstreamError

_model = None


def _load_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(config.stt_model, device="cpu", compute_type=config.stt_compute_type)
    return _model


class LocalProvider:
    """faster-whisper (CTranslate2, CPU). Maps its own failures into the shared taxonomy as transient
    STT_UNAVAILABLE — model-load and decode crashes are infra problems a wedged box recovers from, and a
    genuinely undecodable file has already failed at the ffmpeg extract step (which maps to STT_BAD_AUDIO).
    faster-whisper decodes lazily during iteration, so the list is materialized inside the try."""

    def transcribe(self, wav_path: str) -> list[Segment]:
        try:
            model = _load_model()
            segments, _info = model.transcribe(wav_path)
            return [Segment(s.start, s.end, s.text) for s in segments]
        except Exception as e:
            raise SttUpstreamError(make_error(STT_UNAVAILABLE, f"local stt failed: {e}", TRANSCRIBE))
