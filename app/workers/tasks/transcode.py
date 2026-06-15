from app.workers.base import TranscodeTask
from app.workers.celery_app import app


@app.task(base=TranscodeTask)
def noop() -> str:
    return "transcode ok"
