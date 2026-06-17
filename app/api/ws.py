import asyncio
import json
import logging
from typing import cast
import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.core.config import config
from app.domain.state import TERMINAL
from app.storage.state import get_client

router = APIRouter()
logger = logging.getLogger(__name__)

PING_INTERVAL = 25 # seconds 


def _new_pubsub_client() -> aioredis.Redis:
    # create a dedicated client per socket
    return aioredis.Redis(host=config.redis_host, port=config.redis_port, decode_responses=True)


def _progress_map(rec: dict) -> dict:
    """Extract progress:{preset} fields from the job hash into {preset: float}."""
    return {
        k.split(":", 1)[1]: float(v)
        for k, v in rec.items()
        if k.startswith("progress:")
    }


@router.websocket("/jobs/{job_id}/progress")
async def progress_ws(ws: WebSocket, job_id: str):
    await ws.accept()
    r = get_client()
    ps_client = _new_pubsub_client()
    ps = ps_client.pubsub()
    try:
        # --- snapshot ---
        rec = cast(dict, await r.hgetall(f"job:{job_id}"))
        if not rec:
            await ws.send_json({"type": "error", "code": "NOT_FOUND"})
            await ws.close(code=1008)
            return

        status = rec.get("status", "")
        await ws.send_json({
            "type": "snapshot",
            "status": status,
            "progress": _progress_map(rec),
        })

        # already terminal — send state frame + close immediately
        if status in TERMINAL:
            await ws.send_json({"type": "state", "status": status})
            await ws.close(code=1001)
            return

        # --- subscribe + relay ---
        await ps.subscribe(f"progress:{job_id}")
        ping_task = asyncio.create_task(_ping_loop(ws))
        try:
            async for raw in ps.listen():
                if raw["type"] != "message":
                    continue
                frame = json.loads(raw["data"])
                await ws.send_json({"type": "progress", **frame})

                cur = cast(str | None, await r.hget(f"job:{job_id}", "status"))
                if cur in TERMINAL:
                    await ws.send_json({"type": "state", "status": cur})
                    await ws.close(code=1001)
                    return
        finally:
            ping_task.cancel()

    except WebSocketDisconnect:
        logger.debug("ws disconnect job=%s", job_id)
    except Exception:
        logger.exception("ws error job=%s", job_id)
    finally:
        # always unsubscribe + close the dedicated pub/sub connection — no leaked subscriptions
        await ps.unsubscribe()
        await ps_client.aclose()


async def _ping_loop(ws: WebSocket):
    while True:
        await asyncio.sleep(PING_INTERVAL)
        await ws.send_json({"type": "ping"})