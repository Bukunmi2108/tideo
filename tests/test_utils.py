from datetime import UTC, datetime

from app.api.utils import new_job_id, now_iso


def test_now_iso_is_utc():
    assert datetime.fromisoformat(now_iso()).tzinfo == UTC


def test_job_id_is_fixed_length_and_url_safe():
    job_id = new_job_id()

    assert len(job_id) == 24
    assert job_id.startswith("j_")
    assert set(job_id[2:]) <= set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")
