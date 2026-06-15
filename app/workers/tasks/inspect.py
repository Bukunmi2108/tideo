from app.workers.base import InspectTask
from app.workers.celery_app import app


@app.task(base=InspectTask)
def noop() -> str:
    return "inspect ok"
