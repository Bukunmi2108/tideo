import os
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