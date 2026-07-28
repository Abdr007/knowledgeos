"""Application entrypoint.

Two health endpoints, deliberately different:

    /healthz   liveness  — is this process alive? No dependency checks. An
                           orchestrator that restarts on this must not restart
                           the whole fleet because the database blinked.
    /readyz    readiness — can this process serve traffic? Probes Postgres,
                           Redis and Qdrant, and reports which one is down so
                           the answer is actionable rather than just "no".
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.router import api_router
from app.core.clients import check_redis, check_vector_store
from app.core.config import get_settings
from app.core.errors import AppError, RateLimitError
from app.core.headers import SecurityHeadersMiddleware
from app.core.logging import RequestContextMiddleware, configure_logging
from app.db.session import check_database

settings = get_settings()
configure_logging(level=settings.log_level, json_output=settings.log_json)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "starting",
        extra={
            "app": settings.app_name,
            "environment": settings.environment.value,
            "llm_provider": settings.llm_provider,
            "chat_model": settings.chat_model,
            "embedding_provider": settings.embedding_provider,
            "embedding_model": settings.embedding_model,
        },
    )
    # Deliberately does NOT fail startup on an unreachable dependency. A backend
    # that refuses to boot while Postgres is briefly unavailable turns a
    # thirty-second blip into a crash loop; readiness reports the truth instead.
    #
    # Note the scope of this warning: only *generation* is affected. Embeddings
    # run locally (D2), so ingestion, retrieval and search are fully functional
    # with no provider credential at all.
    if not settings.llm_is_configured and not settings.is_test:
        logger.warning(
            "no API key for the selected LLM provider — chat will fail; "
            "ingestion, search and retrieval are unaffected",
            extra={"llm_provider": settings.llm_provider},
        )
    worker_stop: threading.Event | None = None
    if settings.run_worker_inline:
        from app import worker

        worker_stop = threading.Event()
        worker.start_inline(worker_stop)

    yield

    if worker_stop is not None:
        # Ask the loop to finish its current job rather than abandoning a
        # half-embedded document, which would leave it PROCESSING with nothing
        # left to pick it up.
        worker_stop.set()
    logger.info("shutting down")


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    docs_url="/docs" if settings.expose_docs else None,
    redoc_url="/redoc" if settings.expose_docs else None,
    openapi_url="/openapi.json" if settings.expose_docs else None,
    lifespan=lifespan,
)

# Order matters: Starlette runs middleware in reverse registration order,
# so registering headers first puts it OUTERMOST — its headers are then
# applied to every response, including ones produced by exception handlers.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    """Translate the domain hierarchy to HTTP once, at the boundary (§17).

    Services raise domain errors and never build an HTTPException, which is what
    keeps them framework-free and reusable by the ingestion worker.
    """
    request_id = getattr(request.state, "request_id", None)
    headers: dict[str, str] = {}
    if isinstance(exc, RateLimitError):
        headers["Retry-After"] = str(exc.retry_after)

    # 5xx means we broke something; log it with a stack trace. 4xx is the client
    # being told "no", which is normal traffic and not worth a stack trace.
    if exc.status_code >= 500:
        logger.exception("domain error", extra={"error": exc.error, "path": request.url.path})
    else:
        logger.info(
            "request rejected",
            extra={"error": exc.error, "status": exc.status_code, "path": request.url.path},
        )

    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_payload(request_id),
        headers=headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """422 with the offending fields named, so a client can fix the call."""
    return JSONResponse(
        # Literal rather than the constant: Starlette renamed
        # HTTP_422_UNPROCESSABLE_ENTITY to HTTP_422_UNPROCESSABLE_CONTENT and
        # deprecated the old name. The number is stable; the constant is not.
        status_code=422,
        content={
            "error": "validation_error",
            "detail": exc.errors(),
            "request_id": getattr(request.state, "request_id", None),
        },
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the detail, return the request id.

    The stack trace goes to the log, never to the client — an unhandled
    exception body is a reliable source of table names, file paths and library
    versions. The request id is what lets support tie a user's report to it.
    """
    logger.exception("unhandled exception", extra={"path": request.url.path})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "internal_error",
            "detail": "An unexpected error occurred.",
            "request_id": getattr(request.state, "request_id", None),
        },
    )


app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/healthz", tags=["health"], summary="Liveness")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz", tags=["health"], summary="Readiness")
def readyz() -> JSONResponse:
    # Keyed by the configured backend so the report names what is actually
    # deployed rather than a fixed list of services.
    checks = {
        "database": check_database(),
        "redis": check_redis(),
        f"vectors ({settings.vector_backend})": check_vector_store(),
    }
    ready = all(checks.values())
    return JSONResponse(
        status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
        content={
            "status": "ready" if ready else "degraded",
            "checks": checks,
            # Naming the failing dependency turns a page at 3am into a fix
            # rather than an investigation.
            "failing": [name for name, ok in checks.items() if not ok],
        },
    )


@app.get("/", tags=["health"], include_in_schema=False)
def root() -> dict[str, Any]:
    return {
        "name": settings.app_name,
        "version": app.version,
        "docs": "/docs" if not settings.is_production else None,
    }
