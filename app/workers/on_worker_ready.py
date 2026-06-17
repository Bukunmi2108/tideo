import subprocess
import logging
from celery.signals import worker_ready
from app.storage.db import init_schema
from app.workers import routing

logger = logging.getLogger(__name__)


@worker_ready.connect
def _ensure_schema(**_):
    init_schema()

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