from app.core.config import config
from app.storage.state import get_sync_client

def _ttl() -> int:
    return config.output_ttl_days * 86400

def claim(event_id: str) -> bool:
    """Atomically claim an event_id. True = first time (dispatch it); False = already done."""
    r = get_sync_client()
    return bool(r.set(f"dispatched:{event_id}", "1", nx=True, ex=_ttl()))


def release(event_id: str) -> None:
    """Undo a claim when the enqueue that followed it failed — so re-consume retries, not skips."""
    get_sync_client().delete(f"dispatched:{event_id}")