import base64
import hashlib
import re
from typing import Annotated

from fastapi import Header

from app.api.errors import ApiError

SESSION_HEADER = "X-Tideo-Session"
OWNER_FIELD = "owner_session_hash"
_TOKEN_RE = re.compile(r"^v1\.[A-Za-z0-9_-]{43}$")


def validate_session_token(token: str) -> str:
    if not _TOKEN_RE.fullmatch(token):
        raise ValueError("invalid guest session")
    payload = token.partition(".")[2]
    try:
        decoded = base64.urlsafe_b64decode(payload + "=")
    except ValueError as exc:
        raise ValueError("invalid guest session") from exc
    if len(decoded) != 32:
        raise ValueError("invalid guest session")
    return token


def hash_session_token(token: str) -> str:
    validate_session_token(token)
    return hashlib.sha256(token.encode()).hexdigest()


def require_session(
    token: Annotated[str | None, Header(alias=SESSION_HEADER)] = None,
) -> str:
    if token is None:
        raise ApiError(401, "SESSION_REQUIRED", "guest session is required")
    try:
        return hash_session_token(token)
    except ValueError:
        raise ApiError(401, "INVALID_SESSION", "guest session is invalid") from None


def owns_job(record: dict | None, owner_session_hash: str) -> bool:
    return bool(record and record.get(OWNER_FIELD) == owner_session_hash)


def session_jobs_key(owner_session_hash: str) -> str:
    return f"session:{owner_session_hash}:jobs"
