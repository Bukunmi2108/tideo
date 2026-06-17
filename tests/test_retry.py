from app.domain.errors import (
    ENCODE_FAILED_TRANSIENT,
    ENCODE_TIMEOUT,
    SOURCE_CORRUPT,
    TRANSCODE,
    make_error,
)
from app.workers.retry import (
    MAX_RETRIES,
    RETRY_BASE_SECONDS,
    RETRY_CAP_SECONDS,
    TIMEOUT_MAX_RETRIES,
    backoff_seconds,
    max_retries_for,
)


# ---- retry budget ----

def test_transient_gets_full_budget():
    assert max_retries_for(make_error(ENCODE_FAILED_TRANSIENT, "x", TRANSCODE)) == MAX_RETRIES


def test_timeout_gets_a_single_retry():
    assert max_retries_for(make_error(ENCODE_TIMEOUT, "x", TRANSCODE)) == TIMEOUT_MAX_RETRIES == 1


def test_permanent_gets_zero():
    assert max_retries_for(make_error(SOURCE_CORRUPT, "x", "inspect")) == 0


# ---- full-jitter backoff ----

def test_backoff_stays_within_jitter_window():
    for attempt in range(6):
        ceiling = min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * (2 ** attempt))
        for _ in range(200):
            assert 0.0 <= backoff_seconds(attempt) <= ceiling


def test_backoff_window_doubles_then_caps():
    assert min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * 2 ** 0) == 5
    assert min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * 2 ** 4) == 80
    assert min(RETRY_CAP_SECONDS, RETRY_BASE_SECONDS * 2 ** 5) == 120  # 160 capped


def test_backoff_never_exceeds_cap_even_at_high_attempts():
    assert max(backoff_seconds(20) for _ in range(300)) <= RETRY_CAP_SECONDS
