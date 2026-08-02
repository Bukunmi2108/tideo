import json

from app.core.logging import get_logger
from app.events.envelope import Envelope
from app.events.producer import publish_confirmed
from app.storage.state import EVENT_OUTBOX

log = get_logger()
BATCH_SIZE = 100


def drain(r, limit: int = BATCH_SIZE) -> int:
    """Publish a bounded outbox batch; delete only broker-acknowledged envelopes."""
    cursor = 0
    pending = {}
    while len(pending) < limit:
        cursor, page = r.hscan(
            EVENT_OUTBOX,
            cursor=cursor,
            count=limit - len(pending),
        )
        pending.update(page)
        if cursor == 0:
            break
    delivered = 0
    for event_id, raw in pending.items():
        try:
            publish_confirmed(Envelope(**json.loads(raw)))
        except Exception:
            log.exception("event_outbox_publish_failed", event_id=event_id)
            break
        r.hdel(EVENT_OUTBOX, event_id)
        delivered += 1
    if delivered:
        log.info("event_outbox_drained", delivered=delivered)
    return delivered
