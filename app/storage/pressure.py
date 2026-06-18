import os
import shutil
import time

from app.core.config import config
from app.core.logging import get_logger

log = get_logger()

_CACHE_TTL = 5.0
_checked_at = 0.0
_shedding = False


def our_usage_bytes() -> int:
    total = 0
    for root in (config.uploads_dir, config.output_dir):
        if not root.exists():
            continue
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                try:
                    total += os.lstat(os.path.join(dirpath, name)).st_size
                except OSError:
                    pass
    return total


def free_bytes() -> int:
    return shutil.disk_usage(config.data_dir).free


def is_shedding(used: int, free: int) -> bool:
    return used >= config.storage_budget_bytes or free < config.storage_min_free_bytes


def under_pressure() -> bool:
    global _checked_at, _shedding
    now = time.monotonic()
    if now - _checked_at >= _CACHE_TTL:
        try:
            _shedding = is_shedding(our_usage_bytes(), free_bytes())
        except OSError:
            log.warning("disk_probe_failed")
            _shedding = False
        _checked_at = now
    return _shedding
