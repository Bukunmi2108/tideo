from app.events.envelope import Envelope
from app.events.producer import publish
from app.events.topics import JOB_STARTED
from app.workers.celery_app import app


@app.task
def stub_job(job_id: str) -> dict:
    """Proves the producer works *inside a forked worker*.
    """
    publish(Envelope(JOB_STARTED, job_id, {"note": "stub — real chord in phase 4"}))
    return {"job_id": job_id, "emitted": JOB_STARTED}
