import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse
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
    configure_logging("api")                     # after uvicorn's own setup, so the JSON formatter wins
    ensure_topics()
    init_schema()
    yield
    flush_producer()


FAVICON = Path(__file__).parent / "static" / "favicon.ico"
DOCS_TITLE = "Tideo — adaptive video transcoding"

app = FastAPI(
    title="Tideo",
    version="0.0.1",
    summary="Distributed video transcoding — one source file into an adaptive HLS ladder, encoded in parallel.",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    return FileResponse(FAVICON)


@app.get("/docs", include_in_schema=False)
async def swagger_ui():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url or "/openapi.json", title=DOCS_TITLE,
        swagger_favicon_url="/favicon.ico",
    )


@app.get("/redoc", include_in_schema=False)
async def redoc_ui():
    return get_redoc_html(
        openapi_url=app.openapi_url or "/openapi.json", title=DOCS_TITLE,
        redoc_favicon_url="/favicon.ico",
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

# (name, host, port) for every dependency /readyz probes.
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
    # transient DB outage on a read -> retryable 503; non-transient psycopg2 errors fall through to 500
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
    except (TimeoutError, OSError):
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
