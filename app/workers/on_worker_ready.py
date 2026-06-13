import subprocess
import logging
from celery.signals import worker_ready

logger = logging.getLogger(__name__)

@worker_ready.connect
def _log_toolchain(**_):
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=True)
        
        first_line = result.stdout.splitlines()[0]
        logger.info(f"System Toolchain Verified: {first_line}")
        
    except (FileNotFoundError, subprocess.CalledProcessError) as e:
        logger.error(f"CRITICAL: ffmpeg validation failed! Task queue may malfunction. Error: {e}")