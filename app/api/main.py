import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Response, status
from fastapi.responses import JSONResponse
from app.api.errors import ApiError
from app.api.routes import upload, job
from app.core.config import config
from app.events.admin import ensure_topics
from app.events.producer import flush_producer

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    ensure_topics()
    yield
    flush_producer()


app = FastAPI(title="Tideo", version="0.0.1", lifespan=lifespan)


app.include_router(upload.router)
app.include_router(job.router)

# (name, host, port) for every dependency /readyz probes.
DEPENDENCIES = [
    ("redis", config.redis_host, config.redis_port),
    ("postgres", config.postgres_host, config.postgres_port),
    ("kafka", config.kafka_host, config.kafka_port),
    ("rabbitmq", config.rabbitmq_host, config.rabbitmq_port),
]

@app.exception_handler(ApiError)
async def _api_error(request, exc):
    return JSONResponse(status_code=exc.status, content={"error": {
        "code": exc.code, "message": exc.message, "job_id": exc.job_id, "retryable": exc.retryable}})

@app.exception_handler(Exception)
async def _unhandled(request, exc):
    logger.exception("unhandled error")
    return JSONResponse(status_code=500, content={"error": {
        "code": "INTERNAL", "message": "internal error", "job_id": None, "retryable": False}})

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
