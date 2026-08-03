import pytest
from celery.exceptions import SoftTimeLimitExceeded

from app.workers.cancellation import CancellationUnavailable
from app.workers.stt import local


class Segment:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


def test_transcribe_stops_during_lazy_segment_iteration(monkeypatch):
    class Model:
        def transcribe(self, _path):
            return iter([Segment(0, 1, "one"), Segment(1, 2, "two")]), None

    checks = iter([False, True])
    monkeypatch.setattr(local, "_load_model", lambda: Model())

    with pytest.raises(local.SttCancelled):
        local.transcribe("audio.wav", cancelled=lambda: next(checks))


def test_transcribe_classifies_local_failure(monkeypatch):
    monkeypatch.setattr(
        local,
        "_load_model",
        lambda: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )

    with pytest.raises(local.SttError) as error:
        local.transcribe("audio.wav")

    assert error.value.error.code == "STT_UNAVAILABLE"
    assert error.value.error.message == "transcription service unavailable"


def test_cancellation_outage_is_not_misclassified_as_stt_failure(monkeypatch):
    class Model:
        def transcribe(self, _path):
            return iter([Segment(0, 1, "one")]), None

    monkeypatch.setattr(local, "_load_model", lambda: Model())

    with pytest.raises(CancellationUnavailable):
        local.transcribe(
            "audio.wav",
            cancelled=lambda: (_ for _ in ()).throw(CancellationUnavailable("j")),
        )


def test_soft_timeout_is_not_misclassified_as_model_failure(monkeypatch):
    monkeypatch.setattr(
        local,
        "_load_model",
        lambda: (_ for _ in ()).throw(SoftTimeLimitExceeded()),
    )

    with pytest.raises(SoftTimeLimitExceeded):
        local.transcribe("audio.wav")
