from app.core.config import config
from app.workers.stt.base import SttProvider, SttUpstreamError


def get_provider() -> SttProvider:
    """Provider seam: STT_PROVIDER selects the transport; the rate-limit gate and taxonomy wrap both."""
    if config.stt_provider == "openai":
        from app.workers.stt.openai_provider import OpenAiProvider
        return OpenAiProvider()
    from app.workers.stt.local import LocalProvider
    return LocalProvider()


__all__ = ["get_provider", "SttProvider", "SttUpstreamError"]
