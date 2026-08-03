import base64
import hashlib

import pytest

from app.api.session import hash_session_token, validate_session_token

TOKEN = "v1." + base64.urlsafe_b64encode(bytes(range(32))).decode().rstrip("=")


def test_valid_session_token_hashes_without_retaining_the_bearer_token():
    assert validate_session_token(TOKEN) == TOKEN
    digest = hash_session_token(TOKEN)
    assert digest == hashlib.sha256(TOKEN.encode()).hexdigest()
    assert TOKEN not in digest


@pytest.mark.parametrize(
    "token",
    [
        "",
        "v2." + "A" * 43,
        "v1.short",
        "v1." + "A" * 42 + "+",
        "v1." + "A" * 44,
    ],
)
def test_invalid_session_tokens_are_rejected(token):
    with pytest.raises(ValueError):
        validate_session_token(token)
