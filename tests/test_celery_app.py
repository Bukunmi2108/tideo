from app.workers.celery_app import app

EXPECTED_QUEUES = {"inspect", "transcode", "package", "transcribe", "cleanup", "dead-letter"}


def test_serializer_is_json_only():
    assert app.conf.task_serializer == "json"
    assert app.conf.result_serializer == "json"
    assert app.conf.accept_content == ["json"]


def test_acks_late_and_reject_on_lost():
    assert app.conf.task_acks_late is True
    assert app.conf.task_reject_on_worker_lost is True


def test_default_prefetch_is_one():
    assert app.conf.worker_prefetch_multiplier == 1


def test_all_queues_declared():
    names = {q.name for q in app.conf.task_queues}
    assert EXPECTED_QUEUES <= names


def test_routes_point_at_expected_queues():
    routes = app.conf.task_routes
    for lane in ("inspect", "transcode", "package", "transcribe", "cleanup"):
        assert routes[f"app.workers.tasks.{lane}.*"]["queue"] == lane
