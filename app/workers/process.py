import os
import signal
import subprocess
import time
from collections.abc import Callable

from app.workers.cancellation import JobCancelled


def terminate_group(proc: subprocess.Popen) -> None:
    try:
        process_group = os.getpgid(proc.pid)
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process_group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def run_process(
    argv: list[str],
    *,
    check: bool = False,
    capture_output: bool = False,
    text: bool = False,
    timeout: float | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> subprocess.CompletedProcess:
    if cancelled and cancelled():
        raise JobCancelled
    proc = subprocess.Popen(
        argv,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
        text=text,
        start_new_session=True,
    )
    started = time.monotonic()
    try:
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if cancelled and cancelled():
                    raise JobCancelled
                if timeout is not None and time.monotonic() - started >= timeout:
                    raise subprocess.TimeoutExpired(argv, timeout)
        if cancelled and cancelled():
            raise JobCancelled
    except BaseException:
        terminate_group(proc)
        raise

    result = subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
    if check:
        result.check_returncode()
    return result
