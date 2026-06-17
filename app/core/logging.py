import logging
import sys

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, merge_contextvars

from app.core.config import config

# uvicorn installs its own handlers on these and sets propagate=False; we neutralize them so their
# records bubble to the one root JSON handler instead of printing in a second, bare format.
_FOREIGN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def _add_service(service: str):
    def processor(_logger, _name, event_dict):
        event_dict["service"] = service
        return event_dict
    return processor


def _shared_chain(service: str):
    # run for BOTH structlog events and foreign (stdlib) records, so every line shares one schema
    return [
        merge_contextvars,                       # pulls bound keys (job_id) onto the event
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_service(service),
    ]


def configure_logging(service: str) -> None:
    """Route every log line — our structlog events AND uvicorn/Celery/stdlib records — to one JSON
    schema on stdout (timestamp, level, service, event, + bound contextvars like job_id). Idempotent."""
    shared = _shared_chain(service)

    structlog.configure(
        processors=shared + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,                # stdlib records (incl. uvicorn/celery) get the same chain
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.JSONRenderer(),
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()                        # drop any basicConfig / uvicorn / celery default handler
    root.addHandler(handler)
    root.setLevel(config.log_level.upper())

    for name in _FOREIGN_LOGGERS:
        lg = logging.getLogger(name)
        lg.handlers.clear()
        lg.propagate = True


def get_logger(*args):
    return structlog.get_logger(*args)


def bind_job(job_id: str) -> None:
    """Bind job_id into the logging context; every line emitted afterwards in this context carries it."""
    bind_contextvars(job_id=job_id)


def clear_log_context() -> None:
    clear_contextvars()
