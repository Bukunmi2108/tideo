import uuid, string
from datetime import datetime, timezone

_B62 = string.digits + string.ascii_letters


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def _b62(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n, 62); out = _B62[r] + out
    return out or "0"

def new_job_id() -> str:
    return "j_" + _b62(uuid.uuid4().int).rjust(22, "0")[:22]