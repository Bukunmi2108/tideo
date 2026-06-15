from app.workers.base import TranscribeTask
from app.workers.celery_app import app


@app.task(base=TranscribeTask)
def noop() -> str:
    return "transcribe ok"
