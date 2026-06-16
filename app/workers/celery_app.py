from celery import Celery
from app.core.config import config
from app.workers import routing
from app.workers.on_worker_ready import _log_toolchain

app = Celery(
    "tideo",
    broker=config.celery_broker_url,
    backend=config.celery_result_backend,
    include=[
        "app.workers.tasks.inspect",
        "app.workers.tasks.transcode",
        "app.workers.tasks.rendition",
        "app.workers.tasks.package",
        "app.workers.tasks.transcribe",
        "app.workers.tasks.cleanup",
        "app.workers.tasks.dispatch_stub",
        "app.dispatcher.dispatch",                  # registers fail_job (the chord link_error handler)
    ],
)

app.conf.update(
    task_queues=routing.task_queues,
    task_routes=routing.task_routes,
    task_default_queue="inspect",

    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,

    worker_max_tasks_per_child=20,
    result_expires=86400,
)