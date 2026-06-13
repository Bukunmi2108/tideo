from celery import Celery
from app.core.config import config
from app.workers.on_worker_ready import _log_toolchain

app = Celery(
    "tideo",
    broker=config.celery_broker_url,
    backend=config.celery_result_backend,
)
