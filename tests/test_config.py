from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Config


def _cfg(**overrides):
    # _env_file=None keeps tests hermetic (ignores any on-disk .env).
    return Config(_env_file=None, **overrides)


def test_derived_paths_from_data_dir():
    cfg = _cfg(data_dir="/srv/data")
    assert cfg.uploads_dir == Path("/srv/data/uploads")
    assert cfg.output_dir == Path("/srv/data/output")


def test_typed_defaults():
    cfg = _cfg()
    assert cfg.max_upload_bytes == 4 * 1024**3
    assert cfg.data_dir == Path("/data")
    assert cfg.profile == "dev"
    assert cfg.allowed_origin_list == ["http://localhost:5173"]


def test_malformed_value_fails_naming_it():
    with pytest.raises(ValidationError) as exc:
        _cfg(max_upload_bytes="lots")
    assert "max_upload_bytes" in str(exc.value).lower()


def test_invalid_profile_rejected():
    with pytest.raises(ValidationError):
        _cfg(profile="prod")


def test_allowed_origins_are_normalized_and_validated():
    cfg = _cfg(allowed_origins="https://tideo.vercel.app, http://localhost:5173/")
    assert cfg.allowed_origin_list == [
        "https://tideo.vercel.app",
        "http://localhost:5173",
    ]

    with pytest.raises(ValidationError):
        _cfg(allowed_origins="*")


@pytest.mark.parametrize("value", ["invalid", "3", "0/60", "3/0", "-1/60", "3/-1"])
def test_invalid_stt_rate_limit_rejected(value):
    with pytest.raises(ValidationError):
        _cfg(stt_rate_limit=value)
