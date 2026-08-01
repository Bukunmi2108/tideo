from pathlib import Path

import pytest
from celery.exceptions import Retry

from app.core.ratelimit import Allowed, RetryIn
from app.domain.errors import (
    STT_BAD_AUDIO,
    STT_UNAVAILABLE,
    TRANSCRIBE,
    make_error,
)
from app.domain.vtt import Segment
from app.workers.stt.local import SttError
from app.workers.tasks import transcribe as T

AUDIO_META = {"duration": 30.0, "audio_codec": "aac"}
NO_AUDIO_META = {"duration": 30.0}


class _Retry(Retry):
    """Stand-in for what Celery's Task.retry raises (a Retry subclass) so the task's backstop re-raises it
    instead of swallowing it as an unexpected error."""

    def __init__(self, countdown=None):
        self.countdown = countdown
        super().__init__()


class FakeRedis:
    def __init__(self):
        self.h = {}
        self.sets = {}

    def hset(self, name, field=None, value=None, mapping=None):
        d = self.h.setdefault(name, {})
        if mapping:
            d.update(mapping)
        if field is not None:
            d[field] = value

    def hincrby(self, name, field, n):
        d = self.h.setdefault(name, {})
        d[field] = int(d.get(field, 0)) + n
        return d[field]

    def sadd(self, k, *vals):
        self.sets.setdefault(k, set()).update(vals)

    def srem(self, k, *vals):
        self.sets.setdefault(k, set()).difference_update(vals)

    def scard(self, k):
        return len(self.sets.get(k, set()))

    def delete(self, k):
        self.sets.pop(k, None)
        self.h.pop(k, None)

    def expire(self, k, ttl):
        pass


class FakeTranscriber:
    def __init__(self, segments=None, exc=None):
        self._segments, self._exc = segments, exc

    def transcribe(self, wav_path, cancelled=None):
        if self._exc:
            raise self._exc
        return self._segments


@pytest.fixture
def harness(tmp_path, monkeypatch):
    redis = FakeRedis()
    status_writes = []
    monkeypatch.setattr(T, "get_sync_client", lambda: redis)
    monkeypatch.setattr(T, "is_cancelled", lambda _jid: False)
    monkeypatch.setattr(T, "acquire", lambda *a, **k: Allowed())
    monkeypatch.setattr(T, "extract_audio", lambda src, out: None)
    monkeypatch.setattr(T, "attach_subtitles", lambda jid, dur: True)
    monkeypatch.setattr(T, "update_subtitles", lambda jid, payload: status_writes.append(payload))
    monkeypatch.setattr(T.paths, "output_dir", lambda jid: tmp_path)
    monkeypatch.setattr(T.transcribe, "retry",
                        lambda *a, countdown=None, **k: (_ for _ in ()).throw(_Retry(countdown)))
    return tmp_path, redis, status_writes


def _set_transcriber(monkeypatch, transcriber):
    monkeypatch.setattr(T, "transcribe_audio", transcriber.transcribe)


def test_no_audio_short_circuits_without_transcription(harness, monkeypatch):
    _, _, writes = harness
    monkeypatch.setattr(
        T,
        "acquire",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("rate limit must not be acquired")),
    )
    monkeypatch.setattr(
        T,
        "transcribe_audio",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("transcriber must not be called")),
    )
    out = T.transcribe("j", "/src.mp4", NO_AUDIO_META)
    assert out == {"status": "none"}
    assert writes[-1]["status"] == "none"


def test_cancelled_job_never_transcribes_or_writes_vtt(harness, monkeypatch):
    job_dir, _, writes = harness
    monkeypatch.setattr(T, "is_cancelled", lambda _jid: True, raising=False)
    monkeypatch.setattr(
        T,
        "transcribe_audio",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("transcriber called")),
    )

    out = T.transcribe("j", "/src.mp4", AUDIO_META)

    assert out == {"status": "cancelled"}
    assert not (job_dir / "subtitles.vtt").exists()
    assert writes[-1]["status"] == "cancelled"


def test_rate_limit_reenqueues_with_countdown(harness, monkeypatch):
    job_dir, _, _ = harness
    calls = []

    def extract(_src, out):
        calls.append("extract")
        Path(out).touch()

    def deny(*_args, **_kwargs):
        calls.append("acquire")
        return RetryIn(7.5)

    monkeypatch.setattr(T, "extract_audio", extract)
    monkeypatch.setattr(T, "acquire", deny)
    monkeypatch.setattr(
        T,
        "transcribe_audio",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("gated before transcription")),
    )
    with pytest.raises(_Retry) as ei:
        T.transcribe("j", "/src.mp4", AUDIO_META)
    assert ei.value.countdown == 7.5
    assert calls == ["extract", "acquire"]
    assert not (job_dir / "audio.wav").exists()


def test_success_writes_vtt_and_marks_ready(harness, monkeypatch):
    job_dir, _, writes = harness
    _set_transcriber(monkeypatch, FakeTranscriber(segments=[Segment(0, 1.5, "hello world")]))
    out = T.transcribe("j", "/src.mp4", AUDIO_META)
    assert out == {"status": "ready"}
    assert (job_dir / "subtitles.vtt").read_text().startswith("WEBVTT")
    assert "hello world" in (job_dir / "subtitles.vtt").read_text()
    assert writes[-1] == {"status": "ready", "url": "/jobs/j/subtitles"}


def test_local_stt_unavailable_retries_then_fails_soft(harness, monkeypatch):
    _set_transcriber(monkeypatch, FakeTranscriber(
        exc=SttError(make_error(STT_UNAVAILABLE, "model unavailable", TRANSCRIBE))))
    with pytest.raises(_Retry):                               # first attempt within the budget -> retry
        T.transcribe("j", "/src.mp4", AUDIO_META)

    monkeypatch.setattr(T.config, "stt_max_retries", 0)       # budget exhausted -> fail soft
    _, _, writes = harness
    out = T.transcribe("j", "/src.mp4", AUDIO_META)
    assert out == {"status": "failed"}
    assert writes[-1]["status"] == "failed" and writes[-1]["code"] == STT_UNAVAILABLE
def test_unexpected_exception_still_records_terminal_status(harness, monkeypatch):
    # an unanticipated failure AFTER the STT call (e.g. corrupt manifest in attach_subtitles) must not
    # leave subtitles stuck at "processing" — the backstop records a soft failure. "Silence is not an outcome."
    _, _, writes = harness
    _set_transcriber(monkeypatch, FakeTranscriber(segments=[Segment(0, 1, "hi")]))
    monkeypatch.setattr(T, "attach_subtitles",
                        lambda jid, dur: (_ for _ in ()).throw(RuntimeError("corrupt manifest")))
    out = T.transcribe("j", "/src.mp4", AUDIO_META)
    assert out == {"status": "failed"}
    assert writes[-1]["status"] == "failed" and writes[-1]["code"] == "STT_INTERNAL"


def test_audio_extraction_failure_fails_soft(harness, monkeypatch):
    _, _, writes = harness
    monkeypatch.setattr(T, "extract_audio", lambda src, out: (_ for _ in ()).throw(RuntimeError("ffmpeg")))
    monkeypatch.setattr(
        T,
        "transcribe_audio",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("never reached")),
    )
    out = T.transcribe("j", "/src.mp4", AUDIO_META)
    assert out == {"status": "failed"}
    assert writes[-1]["code"] == STT_BAD_AUDIO
