import time

import httpx

from app.core.config import config
from app.core.logging import configure_logging, get_logger
from app.workers.routing import QUEUE_NAMES

log = get_logger()

STATUS_URL = "http://api:8000/status"
LEASE_URL = "http://workspace-caddy/readyz"
BUSY_STATES = ("inspecting", "queued", "transcoding")
KAFKA_GROUPS = ("dispatcher", "audit")


def _has_work(section, names) -> bool:
    if not isinstance(section, dict) or any(name not in section for name in names):
        return True
    try:
        return any(int(section[name]) > 0 for name in names)
    except (TypeError, ValueError):
        return True


def should_hold_lease(status) -> bool:
    if not isinstance(status, dict):
        return True
    return (
        _has_work(status.get("jobs"), BUSY_STATES)
        or _has_work(status.get("queues"), QUEUE_NAMES)
        or _has_work(status.get("kafka_lag"), KAFKA_GROUPS)
    )


def refresh(client: httpx.Client, host: str) -> bool:
    try:
        response = client.get(STATUS_URL)
        response.raise_for_status()
        hold = should_hold_lease(response.json())
    except (httpx.HTTPError, TypeError, ValueError) as exc:
        log.warning("sleep_status_unavailable", error=str(exc))
        hold = True

    if not hold:
        return False

    response = client.get(LEASE_URL, headers={"Host": host})
    response.raise_for_status()
    return True


def run() -> None:
    if not config.sleep_lease_host:
        raise RuntimeError("SLEEP_LEASE_HOST is required")
    configure_logging("sleep-lease")
    with httpx.Client(timeout=15) as client:
        while True:
            try:
                refresh(client, config.sleep_lease_host)
            except httpx.HTTPError as exc:
                log.error("sleep_lease_refresh_failed", error=str(exc))
            time.sleep(config.sleep_lease_interval_seconds)


if __name__ == "__main__":
    run()
