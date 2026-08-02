import fcntl
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from app.core.config import config


def output_dir(job_id: str) -> Path:
    return config.output_dir / job_id


def ensure_output_dir(job_id: str) -> Path:
    d = output_dir(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


@contextmanager
def path_lock(final: Path):
    lock_path = final.with_name(f".{final.name}.lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def atomic_path(final: Path):
    fd, raw_tmp = tempfile.mkstemp(
        prefix=f"{final.stem}.tmp.", suffix=final.suffix, dir=final.parent
    )
    os.close(fd)
    tmp = Path(raw_tmp)
    try:
        yield tmp
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

@contextmanager
def atomic_dir(final: Path):
    tmp = Path(tempfile.mkdtemp(prefix=f"{final.name}.tmp.", dir=final.parent))
    try:
        yield tmp
        with path_lock(final):
            shutil.rmtree(final, ignore_errors=True)
            os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
