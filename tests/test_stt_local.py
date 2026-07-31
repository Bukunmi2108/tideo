import pytest

from app.workers.stt import local
from app.workers.stt.base import SttCancelled


class Segment:
    def __init__(self, start, end, text):
        self.start = start
        self.end = end
        self.text = text


def test_local_provider_stops_during_lazy_segment_iteration(monkeypatch):
    class Model:
        def transcribe(self, _path):
            return iter([Segment(0, 1, "one"), Segment(1, 2, "two")]), None

    checks = iter([False, True])
    monkeypatch.setattr(local, "_load_model", lambda: Model())

    with pytest.raises(SttCancelled):
        local.LocalProvider().transcribe("audio.wav", cancelled=lambda: next(checks))
