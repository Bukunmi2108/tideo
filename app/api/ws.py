import json
from typing import cast

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.model import error_view, json_list, progress_map, results_view
from app.core.logging import bind_job, get_logger
from app.domain.state import TERMINAL
from app.storage.state import get_client

router = APIRouter()
log = get_logger()

PING_INTERVAL = 25


async def _send_terminal(ws: WebSocket, r, job_id: str, status: str) -> None:
    frame: dict = {"type": "state", "status": status}
    if status in ("done", "failed"):
        rec = cast(dict, await r.hgetall(f"job:{job_id}"))
        if status == "done":
            frame["results"] = results_view(job_id, rec)
        else:
            frame["error"] = error_view(rec)
    await ws.send_json(frame)
    await ws.close(code=1001)


@router.websocket("/jobs/{job_id}/progress")
async def progress_ws(ws: WebSocket, job_id: str) -> None:
    bind_job(job_id)
    await ws.accept()
    r = get_client()
    ps = r.pubsub()
    try:
        # Queue transitions before reading the snapshot; the snapshot is still sent first.
        await ps.subscribe(f"progress:{job_id}")
        rec = cast(dict, await r.hgetall(f"job:{job_id}"))
        if not rec:
            await ws.send_json({"type": "error", "code": "NOT_FOUND"})
            await ws.close(code=1008)
            return

        status = rec.get("status", "")
        await ws.send_json({
            "type": "snapshot",
            "status": status,
            "presets": json_list(rec, "presets", job_id),
            "progress": progress_map(rec, job_id),
        })

        if status in TERMINAL:
            await _send_terminal(ws, r, job_id, status)
            return

        while True:
            raw = await ps.get_message(timeout=PING_INTERVAL)
            if raw is None:
                await ws.send_json({"type": "ping"})
                continue
            if raw["type"] != "message":
                continue
            frame = json.loads(raw["data"])
            if "percent" in frame:
                await ws.send_json({"type": "progress", **frame})

            cur = cast(str | None, await r.hget(f"job:{job_id}", "status"))
            if cur in TERMINAL:
                await _send_terminal(ws, r, job_id, cur)
                return

    except WebSocketDisconnect:
        log.debug("ws_disconnected")
    except Exception:
        log.exception("ws_error")
    finally:
        await ps.aclose()
