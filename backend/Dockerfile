# Multi-stage: dependencies are resolved in a builder and the runtime image
# carries only the virtualenv and the source. Build tooling in a production
# image is both weight and attack surface.

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependency files first, source second. Editing a .py file then only
# invalidates the final layer instead of forcing a full dependency reinstall.
COPY pyproject.toml uv.lock* ./
RUN uv sync --frozen --no-dev --no-install-project 2>/dev/null \
    || uv sync --no-dev --no-install-project

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
RUN uv sync --no-dev 2>/dev/null || true


FROM python:3.12-slim AS runtime

# tini reaps zombies and forwards signals, so `docker stop` is a clean shutdown
# rather than a ten-second wait followed by SIGKILL mid-request.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

# Never root. A container escape should not begin with uid 0.
RUN useradd --create-home --shell /bin/bash --uid 10001 app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/app /app/app
COPY --from=builder --chown=app:app /app/alembic /app/alembic
COPY --from=builder --chown=app:app /app/alembic.ini /app/alembic.ini

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER app
EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
# --proxy-headers so client IPs survive the load balancer, which matters for
# rate limiting and audit logs.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
