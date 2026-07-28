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

# The project is NOT installed as a package, deliberately.
#
# `uv sync` without --no-install-project builds a wheel into site-packages, so
# `app` would exist twice: once there and once at /app/app. Which one Python
# imports depends on sys.path order, and a wheel built from a cached layer
# silently shadows the real source — producing ModuleNotFoundError for a module
# that is visibly present in the image.
#
# This is a service, not a library. It is imported from the working directory,
# with PYTHONPATH set explicitly in the runtime stage so resolution cannot drift.


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

# Mount points for the named volumes, created HERE and owned by the app user.
#
# Docker seeds a fresh named volume from whatever is at the mount path in the
# image, ownership included. If the path does not exist in the image, the volume
# is created owned by root — and a container that correctly runs as non-root then
# cannot write to its own storage. Uploads fail with PermissionError, and only in
# the containerised deployment, never in local development.
RUN mkdir -p /data/storage /home/app/.cache \
    && chown -R app:app /data /home/app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/home/app

COPY --chown=app:app entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

USER app
EXPOSE 8000

# tini reaps zombies and forwards signals, so `docker stop` is a clean shutdown
# rather than a ten-second wait followed by SIGKILL mid-request.
ENTRYPOINT ["/usr/bin/tini", "--", "/app/entrypoint.sh"]
