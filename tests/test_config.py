from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import Config


def _cfg(**overrides):
    # _env_file=None keeps tests hermetic (ignores any on-disk .env).
    return Config(_env_file=None, admin_token="t", **overrides)


def test_derived_paths_from_data_dir():
    cfg = _cfg(data_dir="/srv/data")
    assert cfg.uploads_dir == Path("/srv/data/uploads")
    assert cfg.output_dir == Path("/srv/data/output")


def test_typed_defaults():
    cfg = _cfg()
    assert cfg.max_upload_bytes == 4 * 1024**3
    assert cfg.data_dir == Path("/data")
    assert cfg.profile == "dev"


def test_missing_required_var_fails_naming_it(monkeypatch):
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    with pytest.raises(ValidationError) as exc:
        Config(_env_file=None)
    assert "admin_token" in str(exc.value).lower()


def test_malformed_value_fails_naming_it():
    with pytest.raises(ValidationError) as exc:
        _cfg(max_upload_bytes="lots")
    assert "max_upload_bytes" in str(exc.value).lower()


def test_invalid_profile_rejected():
    with pytest.raises(ValidationError):
        _cfg(profile="prod")
