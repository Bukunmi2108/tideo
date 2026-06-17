import asyncio
import json
from typing import cast
import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from app.api.model import progress_map, results_view
from app.core.config import config
from app.core.logging import bind_job, get_logger
from app.domain.state import TERMINAL
from app.storage.state import get_client

router = APIRouter()
log = get_logger()

PING_INTERVAL = 25 # seconds


def _new_pubsub_client() -> aioredis.Redis:
    # create a dedicated client per socket
    return aioredis.Redis(host=config.redis_host, port=config.redis_port, decode_responses=True)


async def _send_terminal(ws: WebSocket, r, job_id: str, status: str) -> None:
    """Send the terminal state frame, then close. `done` carries results, `failed` carries the error envelope."""
    frame: dict = {"type": "state", "status": status}
    if status in ("done", "failed"):
        rec = cast(dict, await r.hgetall(f"job:{job_id}"))
        if status == "done":
            frame["results"] = results_view(job_id, rec)
        else:
            frame["error"] = {
                "code": rec.get("error_code"), "message": rec.get("error_message"),
                "stage": rec.get("error_stage"), "retryable": False,
            }
    await ws.send_json(frame)
    await ws.close(code=1001)


@router.websocket("/jobs/{job_id}/progress")
async def progress_ws(ws: WebSocket, job_id: str):
    bind_job(job_id)
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
            "presets": json.loads(rec["presets"]) if rec.get("presets") else [],
            "progress": progress_map(rec),
        })

        # already terminal — send state frame + close immediately
        if status in TERMINAL:
            await _send_terminal(ws, r, job_id, status)
            return

        # --- subscribe + relay ---
        await ps.subscribe(f"progress:{job_id}")
        ping_task = asyncio.create_task(_ping_loop(ws))
        try:
            async for raw in ps.listen():
                if raw["type"] != "message":
                    continue
                frame = json.loads(raw["data"])
                if "percent" in frame:  # progress frame; terminal pokes carry no percent
                    await ws.send_json({"type": "progress", **frame})

                cur = cast(str | None, await r.hget(f"job:{job_id}", "status"))
                if cur in TERMINAL:
                    await _send_terminal(ws, r, job_id, cur)
                    return
        finally:
            ping_task.cancel()

    except WebSocketDisconnect:
        log.debug("ws_disconnected")
    except Exception:
        log.exception("ws_error")
    finally:
        # always unsubscribe + close the dedicated pub/sub connection — no leaked subscriptions
        await ps.unsubscribe()
        await ps_client.aclose()


async def _ping_loop(ws: WebSocket):
    while True:
        await asyncio.sleep(PING_INTERVAL)
        await ws.send_json({"type": "ping"})