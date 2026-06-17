import time

import httpx
from confluent_kafka import Consumer, TopicPartition
from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from app.api.utils import now_iso
from app.core.config import config
from app.core.logging import get_logger
from app.domain.state import ACTIVE
from app.events.topics import TOPIC
from app.storage import db
from app.storage.state import ACTIVE_COUNTS, get_client
from app.workers.dlq import DLQ_KEY
from app.workers.routing import QUEUE_NAMES

router = APIRouter(tags=["Status"])
log = get_logger()

_KAFKA_GROUPS = ("dispatcher", "audit")
_CACHE_TTL = 5.0
_cache: dict = {"at": 0.0, "data": None}


async def _jobs_section() -> dict:
    r = get_client()
    active_raw = await r.hgetall(ACTIVE_COUNTS)
    active = {s: max(0, int(active_raw.get(s, 0))) for s in sorted(ACTIVE)}   # clamp drift to >= 0
    terminal = await run_in_threadpool(db.count_by_status)
    return {**active, **terminal}


async def _disk_section() -> dict:
    pct = round((await run_in_threadpool(_safe_disk_pct)), 1)
    return {"used_pct": pct, "watermark_pct": config.storage_watermark_pct,
            "shedding": pct >= config.storage_watermark_pct}


def _safe_disk_pct() -> float:
    from app.storage.pressure import usage_pct
    return usage_pct()


async def _dispatcher_section() -> dict:
    r = get_client()
    beat = await r.get("dispatcher:heartbeat")
    if not beat:
        return {"alive": False, "last_beat": None}
    return {"alive": True, "last_beat": beat}


async def _dlq_section() -> dict:
    return {"depth": await get_client().hlen(DLQ_KEY)}


async def _queues_section() -> dict:
    auth = (config.rabbitmq_user, config.rabbitmq_password)
    url = f"http://{config.rabbitmq_host}:{config.rabbitmq_mgmt_port}/api/queues"
    async with httpx.AsyncClient(timeout=3.0) as client:
        resp = await client.get(url, auth=auth)
        resp.raise_for_status()
        depths = {q["name"]: q.get("messages", 0) for q in resp.json()}
    return {name: depths.get(name, 0) for name in QUEUE_NAMES}


def _group_lag(group: str) -> int:
    c = Consumer({"bootstrap.servers": config.kafka_bootstrap, "group.id": group,
                  "enable.auto.commit": False})
    try:
        md = c.list_topics(TOPIC, timeout=5)
        if TOPIC not in md.topics or md.topics[TOPIC].error is not None:
            return 0
        tps = [TopicPartition(TOPIC, p) for p in md.topics[TOPIC].partitions]
        lag = 0
        for tp in c.committed(tps, timeout=5):           # committed() queries the broker; no group join
            _, hi = c.get_watermark_offsets(tp, timeout=5)
            committed = tp.offset if tp.offset >= 0 else 0   # -1001 => no commit yet
            lag += max(0, hi - committed)
        return lag
    finally:
        c.close()


async def _kafka_section() -> dict:
    return {g: await run_in_threadpool(_group_lag, g) for g in _KAFKA_GROUPS}


_SECTIONS = {
    "jobs": _jobs_section,
    "disk": _disk_section,
    "queues": _queues_section,
    "kafka_lag": _kafka_section,
    "dispatcher": _dispatcher_section,
    "dlq": _dlq_section,
}


async def _build() -> dict:
    out: dict = {"generated_at": now_iso()}
    for name, fn in _SECTIONS.items():
        try:
            out[name] = await fn()
        except Exception as e:
            # the status page must work *especially* when something is broken — degrade, don't 500
            log.warning("status_section_unreachable", section=name, error=str(e))
            out[name] = "unreachable"
    return out


@router.get("/status")
async def status():
    now = time.monotonic()
    if _cache["data"] is not None and now - _cache["at"] < _CACHE_TTL:
        return _cache["data"]
    data = await _build()
    _cache.update(at=now, data=data)
    return data
