from app.workers.base import PackageTask
from app.workers.celery_app import app


@app.task(base=PackageTask)
def noop() -> str:
    return "package ok"
