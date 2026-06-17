import shutil
import time

from app.core.config import config
from app.core.logging import get_logger

log = get_logger()

_CACHE_TTL = 5.0
_checked_at = 0.0
_pct = 0.0


def usage_pct() -> float:
    u = shutil.disk_usage(config.data_dir)
    return u.used / u.total * 100.0


def under_pressure() -> bool:
    """True when disk usage is at/over the watermark. Cached a few seconds so it's ~free per request.
    Fail-open: if usage can't be read, don't shed — the expiry sweep is the disk backstop, and blocking
    all new work on a stat hiccup is worse than admitting it."""
    global _checked_at, _pct
    now = time.monotonic()
    if now - _checked_at >= _CACHE_TTL:
        try:
            _pct = usage_pct()
        except OSError:
            log.warning("disk_probe_failed")
            _pct = 0.0
        _checked_at = now
    return _pct >= config.storage_watermark_pct
