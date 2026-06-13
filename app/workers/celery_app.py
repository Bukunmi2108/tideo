from celery import Celery

from app.core.config import config

app = Celery(
    "tideo",
    broker=config.celery_broker_url,
    backend=config.celery_result_backend,
)
