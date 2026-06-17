from pathlib import Path

import pytest

from app.domain import errors
from app.domain.errors import (
    ENCODE_FAILED_TRANSIENT,
    ENCODE_TIMEOUT,
    SOURCE_CORRUPT,
    SOURCE_UNSUPPORTED,
    classify,
    is_retryable,
    make_error,
)

CORPUS = Path(__file__).parent / "fixtures" / "stderr"


def _corpus_cases():
    return [(p, p.parent.name) for p in sorted(CORPUS.rglob("*.txt"))]


@pytest.mark.parametrize("sample,expected_code", _corpus_cases(), ids=lambda v: getattr(v, "name", v))
def test_classifier_matches_corpus(sample: Path, expected_code: str):
    err = classify(1, sample.read_text(), stage="transcode")
    assert err.code == expected_code
    assert err.stage == "transcode"


def test_corpus_covers_each_pattern_code():
    covered = {p.parent.name for p in CORPUS.rglob("*.txt")}
    assert {SOURCE_CORRUPT, SOURCE_UNSUPPORTED, "STORAGE_FULL"} <= covered


# ---- exit-code paths (no stderr regex) ----

@pytest.mark.parametrize("rc", [137, 143, -9])
def test_signal_death_is_transient(rc):
    err = classify(rc, "")
    assert err.code == ENCODE_FAILED_TRANSIENT
    assert err.retryable is True


def test_unclassified_defaults_to_transient():
    err = classify(1, "some unrecognized ffmpeg complaint")
    assert err.code == ENCODE_FAILED_TRANSIENT
    assert err.retryable is True


def test_corrupt_pattern_wins_over_codec_when_both_present():
    err = classify(1, "moov atom not found\nDecoder not found")
    assert err.code == SOURCE_CORRUPT  # corrupt patterns are ordered first


# ---- retryable registry ----

def test_retryable_registry():
    assert is_retryable(SOURCE_CORRUPT) is False
    assert is_retryable(ENCODE_TIMEOUT) is True
    assert is_retryable(errors.STORAGE_FULL) is False
    assert is_retryable("SOMETHING_NEW") is True  # unknown -> transient


def test_make_error_fills_retryable_from_registry():
    err = make_error(SOURCE_UNSUPPORTED, "Decoder not found", "transcode")
    assert err.retryable is False
    assert err.code == SOURCE_UNSUPPORTED
