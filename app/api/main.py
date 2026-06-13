import asyncio

from fastapi import FastAPI, Response, status

from app.core.config import config

app = FastAPI(title="Tideo", version="0.0.1")

# (name, host, port) for every dependency /readyz probes.
DEPENDENCIES = [
    ("redis", config.redis_host, config.redis_port),
    ("postgres", config.postgres_host, config.postgres_port),
    ("kafka", config.kafka_host, config.kafka_port),
    ("rabbitmq", config.rabbitmq_host, config.rabbitmq_port),
]


@app.get("/healthz")
def healthz():
    """Liveness Check"""
    return {"status": "ok"}


async def _probe(host: str, port: int) -> bool:
    """TCP-connect probe: proves the port is accepting connections, bounded by a timeout, and never raises — any failure means 'not ready'.
    """
    writer = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=config.readiness_timeout_seconds,
        )
        return True
    except (OSError, asyncio.TimeoutError):
        return False
    finally:
        if writer is not None:
            writer.close()


@app.get("/readyz")
async def readyz(response: Response):
    """Readiness Check"""
    results = await asyncio.gather(
        *(_probe(host, port) for _, host, port in DEPENDENCIES)
    )
    failing = [name for (name, _, _), ok in zip(DEPENDENCIES, results) if not ok]
    if failing:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": not failing, "failing": failing}
