from app.core.config import config
from app.workers.stt.base import SttCancelled, SttProvider, SttUpstreamError


def get_provider() -> SttProvider:
    """Build the configured STT provider."""
    if config.stt_provider == "openai":
        from app.workers.stt.openai_provider import OpenAiProvider
        return OpenAiProvider()
    from app.workers.stt.local import LocalProvider
    return LocalProvider()


__all__ = ["SttCancelled", "SttProvider", "SttUpstreamError", "get_provider"]
