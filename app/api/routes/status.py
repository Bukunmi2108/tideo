import asyncio
import time

import httpx
from confluent_kafka import Consumer, TopicPartition
from fastapi import APIRouter

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
_SECTION_TIMEOUT = 10.0
_cache: dict = {"at": 0.0, "data": None}
_cache_lock = asyncio.Lock()


async def _jobs_section() -> dict:
    r = get_client()
    active_raw = await r.hgetall(ACTIVE_COUNTS)
    active = {s: max(0, int(active_raw.get(s, 0))) for s in sorted(ACTIVE)}
    terminal = await asyncio.to_thread(db.count_by_status)
    return {**active, **terminal}


async def _disk_section() -> dict:
    return await asyncio.to_thread(_disk_snapshot)


def _disk_snapshot() -> dict:
    from app.storage.pressure import free_bytes, is_shedding, our_usage_bytes

    used, free = our_usage_bytes(), free_bytes()
    return {
        "used_bytes": used,
        "budget_bytes": config.storage_budget_bytes,
        "free_bytes": free,
        "shedding": is_shedding(used, free),
    }


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
    missing = sorted(set(QUEUE_NAMES) - depths.keys())
    if missing:
        raise RuntimeError(f"RabbitMQ queues missing: {', '.join(missing)}")
    return {name: depths[name] for name in QUEUE_NAMES}


def _group_lag(group: str) -> int:
    c = Consumer({"bootstrap.servers": config.kafka_bootstrap, "group.id": group,
                  "enable.auto.commit": False})
    try:
        md = c.list_topics(TOPIC, timeout=5)
        topic = md.topics.get(TOPIC)
        if topic is None:
            raise RuntimeError(f"Kafka topic missing: {TOPIC}")
        if topic.error is not None:
            raise RuntimeError(f"Kafka topic unavailable: {topic.error}")
        if not topic.partitions:
            raise RuntimeError(f"Kafka topic has no partitions: {TOPIC}")
        tps = [TopicPartition(TOPIC, p) for p in topic.partitions]
        lag = 0
        for tp in c.committed(tps, timeout=5):
            if tp.error is not None:
                raise RuntimeError(f"Kafka committed offset unavailable: {tp.error}")
            low, hi = c.get_watermark_offsets(tp, timeout=5)
            committed = max(tp.offset, low) if tp.offset >= 0 else low
            lag += max(0, hi - committed)
        return lag
    finally:
        c.close()


async def _kafka_section() -> dict:
    values = await asyncio.gather(*(asyncio.to_thread(_group_lag, group) for group in _KAFKA_GROUPS))
    return dict(zip(_KAFKA_GROUPS, values, strict=True))


_SECTIONS = {
    "jobs": _jobs_section,
    "disk": _disk_section,
    "queues": _queues_section,
    "kafka_lag": _kafka_section,
    "dispatcher": _dispatcher_section,
    "dlq": _dlq_section,
}


async def _run_section(name: str, fn) -> tuple[str, dict | str]:
    try:
        value = await asyncio.wait_for(fn(), timeout=_SECTION_TIMEOUT)
        return name, value
    except Exception as exc:  # noqa: BLE001
        error = "timeout" if isinstance(exc, TimeoutError) else str(exc)
        log.warning("status_section_unreachable", section=name, error=error)
        return name, "unreachable"


async def _build() -> dict:
    out: dict = {"generated_at": now_iso()}
    sections = await asyncio.gather(*(
        _run_section(name, fn) for name, fn in _SECTIONS.items()
    ))
    out.update(sections)
    return out


def _cached(now: float) -> bool:
    return _cache["data"] is not None and now - _cache["at"] < _CACHE_TTL


@router.get("/status")
async def status():
    if _cached(time.monotonic()):
        return _cache["data"]
    async with _cache_lock:
        if _cached(time.monotonic()):
            return _cache["data"]
        data = await _build()
        _cache.update(at=time.monotonic(), data=data)
        return data
