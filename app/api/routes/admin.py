import json
from fastapi import APIRouter, Depends, Header
from app.api.errors import ApiError
from app.core.config import config
from app.storage.state import get_client
from app.workers.celery_app import app as celery_app
from app.workers.dlq import DLQ_KEY


async def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    if not x_admin_token or x_admin_token != config.admin_token:
        raise ApiError(401, "UNAUTHORIZED", "admin token required")


router = APIRouter(prefix="/admin", tags=["Admin"], dependencies=[Depends(require_admin)])


@router.get("/dlq")
async def list_dlq():
    raw = await get_client().hgetall(DLQ_KEY)
    records = [json.loads(v) for v in raw.values()]
    records.sort(key=lambda r: r.get("failed_at", ""), reverse=True)
    return {"records": records}


@router.post("/dlq/{record_id}/requeue", status_code=202)
async def requeue(record_id: str):
    r = get_client()
    raw = await r.hget(DLQ_KEY, record_id)
    if not raw:
        raise ApiError(404, "NOT_FOUND", "no such dlq record")
    rec = json.loads(raw)
    celery_app.send_task(rec["task"], args=rec["args"])  # fresh send -> retries reset
    await r.hdel(DLQ_KEY, record_id)
    return {"requeued": record_id, "task": rec["task"]}


@router.delete("/dlq/{record_id}")
async def discard(record_id: str):
    if not await get_client().hdel(DLQ_KEY, record_id):
        raise ApiError(404, "NOT_FOUND", "no such dlq record")
    return {"discarded": record_id}
