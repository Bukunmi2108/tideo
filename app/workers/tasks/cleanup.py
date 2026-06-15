from app.workers.base import CleanupTask
from app.workers.celery_app import app


@app.task(base=CleanupTask)
def noop() -> str:
    return "cleanup ok"
