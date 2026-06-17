import httpx

from app.core.config import config
from app.domain.errors import STT_BAD_AUDIO, STT_RATE_LIMITED, STT_UNAVAILABLE, TRANSCRIBE, make_error
from app.domain.vtt import Segment
from app.workers.stt.base import SttUpstreamError
from app.workers.stt.retry_after import parse_retry_after


class OpenAiProvider:
    """OpenAI Whisper API. Maps HTTP status to the shared taxonomy: 429 -> rate-limited (honor
    Retry-After), 5xx/timeout/connection -> unavailable (transient), 400/422 -> bad audio (permanent)."""

    def transcribe(self, wav_path: str) -> list[Segment]:
        try:
            with open(wav_path, "rb") as f:
                resp = httpx.post(
                    f"{config.stt_api_base}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {config.openai_api_key}"},
                    data={"model": "whisper-1", "response_format": "verbose_json"},
                    files={"file": ("audio.wav", f, "audio/wav")},
                    timeout=120,
                )
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise SttUpstreamError(make_error(STT_UNAVAILABLE, f"upstream unreachable: {e}", TRANSCRIBE))

        if resp.status_code == 429:
            raise SttUpstreamError(make_error(STT_RATE_LIMITED, "upstream rate limited", TRANSCRIBE),
                                   retry_after=parse_retry_after(resp.headers.get("Retry-After")))
        if resp.status_code >= 500:
            raise SttUpstreamError(make_error(STT_UNAVAILABLE, f"upstream {resp.status_code}", TRANSCRIBE))
        if resp.status_code in (400, 422):
            raise SttUpstreamError(make_error(STT_BAD_AUDIO, f"upstream rejected audio ({resp.status_code})", TRANSCRIBE))
        resp.raise_for_status()

        return [Segment(s.get("start", 0.0), s.get("end", 0.0), s.get("text", ""))
                for s in resp.json().get("segments", [])]
