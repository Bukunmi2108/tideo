import os
import shutil
from contextlib import contextmanager
from pathlib import Path
from app.core.config import config

def output_dir(job_id: str) -> Path:
    d = config.output_dir / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d

@contextmanager
def atomic_path(final: Path):
    tmp = final.with_name(f"{final.stem}.tmp{final.suffix}")
    try:
        yield tmp
        os.replace(tmp, final)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

@contextmanager
def atomic_dir(final: Path):
    """Build into a temp dir, swap it into place on success. A crash leaves only the temp.
    Idempotent: a retry overwrites any partial final, so the final dir only ever appears complete."""
    tmp = final.with_name(f"{final.name}.tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        yield tmp
        shutil.rmtree(final, ignore_errors=True)
        os.replace(tmp, final)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise