import subprocess
import sys

import pytest

from app.workers.cancellation import JobCancelled
from app.workers.process import run_process


def test_cancelled_process_is_terminated():
    checks = 0

    def cancelled():
        nonlocal checks
        checks += 1
        return checks > 1

    with pytest.raises(JobCancelled):
        run_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cancelled=cancelled,
        )

    assert checks > 1


def test_timed_out_process_is_terminated():
    with pytest.raises(subprocess.TimeoutExpired):
        run_process(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            timeout=0.1,
        )
