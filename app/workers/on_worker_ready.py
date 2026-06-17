import subprocess
import logging
from celery.signals import setup_logging, task_postrun, worker_ready
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError
from app.core.logging import clear_log_context, configure_logging
from app.storage.db import init_schema
from app.workers import routing

logger = logging.getLogger(__name__)


@setup_logging.connect
def _configure_worker_logging(**_):
    # connecting this signal stops Celery installing its own handlers — we own the format instead
    configure_logging("worker")


@task_postrun.connect
def _clear_job_context(**_):
    clear_log_context()                          # prefork reuses processes; don't let job_id bleed across tasks


@worker_ready.connect
def _ensure_schema(**_):
    init_schema()


@worker_ready.connect
def _boot_sweep(**_):
    # Beat is silent while a sleeping Space is down, so expire-on-wake here. NX guard -> one sweep per boot.
    from app.storage.state import get_sync_client          # imports outside the try: a broken import is a bug, not "infra down"
    from app.workers.celery_app import app
    try:
        if get_sync_client().set("cleanup:boot", "1", nx=True, ex=120):
            app.send_task("app.workers.tasks.cleanup.sweep")
    except (RedisError, OperationalError):
        logger.exception("boot sweep enqueue failed (continuing)")

@worker_ready.connect
def _log_toolchain(**_):
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True)
        first_line = result.stdout.splitlines()[0]
        logger.info(f"System Toolchain Verified: {first_line}")
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        logger.error(f"CRITICAL: ffmpeg validation failed! Task queue may malfunction. Error: {e}")

@worker_ready.connect
def _declare_queues(sender, **_):
    with sender.app.connection_for_write() as conn:
        channel = conn.default_channel
        for queue in routing.task_queues:
            queue.bind(channel).declare()