import pytest

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
