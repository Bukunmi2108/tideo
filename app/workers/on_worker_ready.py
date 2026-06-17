import subprocess
import logging
from celery.signals import worker_ready
from kombu.exceptions import OperationalError
from redis.exceptions import RedisError
from app.storage.db import init_schema
from app.workers import routing

logger = logging.getLogger(__name__)


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