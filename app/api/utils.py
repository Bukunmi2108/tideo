from datetime import UTC, datetime
from secrets import token_urlsafe


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def new_job_id() -> str:
    return f"j_{token_urlsafe(16)}"
