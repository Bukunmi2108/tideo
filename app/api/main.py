import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from psycopg2 import InterfaceError, OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api import ws as ws_module
from app.api.errors import ApiError, error_response
from app.api.routes import artifacts, job, upload
from app.api.routes import status as status_routes
from app.core.config import config
from app.core.logging import configure_logging, get_logger
from app.events.admin import ensure_topics
from app.events.producer import flush_producer
from app.storage.db import init_schema

log = get_logger()


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging("api")
    ensure_topics()
    init_schema()
    yield
    flush_producer()


app = FastAPI(
    title="Tideo",
    version="0.0.1",
    summary="Distributed video transcoding — one source file into an adaptive HLS ladder, encoded in parallel.",
    lifespan=lifespan,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


app.include_router(upload.router)
app.include_router(job.router)
app.include_router(artifacts.router)
app.include_router(status_routes.router)
app.include_router(ws_module.router)

DEPENDENCIES = [
    ("redis", config.redis_host, config.redis_port),
    ("postgres", config.postgres_host, config.postgres_port),
    ("kafka", config.kafka_host, config.kafka_port),
    ("rabbitmq", config.rabbitmq_host, config.rabbitmq_port),
]

@app.exception_handler(ApiError)
async def _api_error(_request: Request, exc: ApiError):
    return error_response(exc.status, exc.code, exc.message, exc.job_id, exc.retryable)


@app.exception_handler(RequestValidationError)
async def _validation_error(request: Request, _exc: RequestValidationError):
    return error_response(
        422,
        "VALIDATION_ERROR",
        "request validation failed",
        request.path_params.get("job_id"),
    )


@app.exception_handler(StarletteHTTPException)
async def _http_error(request: Request, exc: StarletteHTTPException):
    code = {
        404: "ROUTE_NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
    }.get(exc.status_code, "HTTP_ERROR")
    message = exc.detail if isinstance(exc.detail, str) else "request failed"
    return error_response(
        exc.status_code,
        code,
        message,
        request.path_params.get("job_id"),
        headers=exc.headers,
    )


async def _db_unavailable(_request: Request, exc: Exception):
    log.warning("db_unavailable_on_read", error=str(exc))
    return error_response(
        503,
        "DB_UNAVAILABLE",
        "service temporarily unavailable, retry shortly",
        retryable=True,
    )

app.add_exception_handler(OperationalError, _db_unavailable)
app.add_exception_handler(InterfaceError, _db_unavailable)


@app.exception_handler(Exception)
async def _unhandled(_request: Request, _exc: Exception):
    log.exception("unhandled_error")
    return error_response(500, "INTERNAL", "internal error")

@app.get("/healthz")
def healthz():
    return {"status": "ok"}


async def _probe(host: str, port: int) -> bool:
    writer = None
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=config.readiness_timeout_seconds,
        )
        return True
    except (TimeoutError, OSError):
        return False
    finally:
        if writer is not None:
            writer.close()


@app.get("/readyz")
async def readyz(response: Response):
    results = await asyncio.gather(
        *(_probe(host, port) for _, host, port in DEPENDENCIES)
    )
    failing = [name for (name, _, _), ok in zip(DEPENDENCIES, results) if not ok]
    if failing:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": not failing, "failing": failing}
