from datetime import datetime, timedelta, timezone

from app.workers.stt.retry_after import parse_retry_after


def test_none_and_blank():
    assert parse_retry_after(None) is None
    assert parse_retry_after("") is None


def test_delta_seconds():
    assert parse_retry_after("30") == 30.0
    assert parse_retry_after("  5 ") == 5.0


def test_http_date_returns_seconds_from_now():
    when = datetime.now(timezone.utc) + timedelta(seconds=120)
    stamp = when.strftime("%a, %d %b %Y %H:%M:%S GMT")
    secs = parse_retry_after(stamp)
    assert secs is not None and 100 <= secs <= 125     # ~120s, tolerant of test clock skew


def test_past_http_date_clamps_to_zero():
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=60)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(stamp) == 0.0


def test_garbage_returns_none():
    assert parse_retry_after("soon") is None
