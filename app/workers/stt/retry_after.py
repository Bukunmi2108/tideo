from datetime import datetime, timezone
from email.utils import parsedate_to_datetime


def parse_retry_after(value: str | None) -> float | None:
    """RFC 7231 Retry-After: delta-seconds (an integer) or an HTTP-date. Returns seconds to wait, or None."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
