"""Structured logging and request correlation.

Logs are JSON in every environment except local, because the first thing anyone
does with production logs is filter them, and you cannot filter prose reliably.

Every log line carries a ``request_id``. It is propagated through a context
variable rather than passed down the call stack, so a service five layers deep
can log without every intermediate function taking a parameter it does not use.
The id is echoed back on the response as ``X-Request-ID``, which is what turns a
user saying "it failed around 3pm" into a single grep.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

#: LogRecord attributes that are structure, not payload. Anything outside this
#: set was attached by a caller via `extra=` and belongs in the JSON output.
_STANDARD_ATTRS = frozenset(
    """args asctime created exc_info exc_text filename funcName levelname levelno
    lineno module msecs message msg name pathname process processName
    relativeCreated stack_info thread threadName taskName""".split()
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


class HumanFormatter(logging.Formatter):
    """Readable output for local development, where a person reads the log."""

    def format(self, record: logging.LogRecord) -> str:
        rid = request_id_var.get()
        prefix = f"\033[2m{rid[:8]}\033[0m " if rid != "-" else ""
        base = f"{prefix}\033[2m{record.name}\033[0m {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(*, level: str = "INFO", json_output: bool = True) -> None:
    root = logging.getLogger()
    root.setLevel(level.upper())
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else HumanFormatter())
    root.addHandler(handler)

    # Uvicorn installs its own handlers; let ours own the output so every line
    # in the container has one shape.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # These are useful at DEBUG and deafening at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, log the outcome, and time the request.

    An inbound ``X-Request-ID`` is honoured so a trace survives across the
    frontend and any proxy in front of it, but it is length-capped: the value is
    attacker-controlled and ends up in every log line for that request.
    """

    MAX_ID_LENGTH = 64

    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get("X-Request-ID", "")
        request_id = incoming[: self.MAX_ID_LENGTH] if incoming else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        request.state.request_id = request_id

        logger = logging.getLogger("app.request")
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                },
            )
            raise
        finally:
            request_id_var.reset(token)

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        # Health probes fire constantly and would drown everything else.
        if request.url.path not in {"/healthz", "/readyz"}:
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                },
            )
        return response
