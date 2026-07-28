#!/bin/sh
# Role selector.
#
# One image runs the API, the worker, or a migration, chosen by KOS_ROLE. Some
# platforms (Railway, Cloud Run) give you an image and environment variables but
# no convenient way to override the command per service, so the role has to be
# selectable from the environment rather than from argv.
#
# An explicit command still wins: `docker run … python -m app.worker` and the
# Compose `command:` override both bypass this entirely.

set -e

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# Platforms assign the listen port at runtime. Defaulting to 8000 keeps Compose
# and local development unchanged.
PORT="${PORT:-8000}"

case "${KOS_ROLE:-api}" in
    api)
        # --proxy-headers so client IPs survive the load balancer, which matters
        # for rate limiting and audit records.
        exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers
        ;;
    worker)
        exec python -m app.worker
        ;;
    migrate)
        exec alembic upgrade head
        ;;
    *)
        echo "Unknown KOS_ROLE: '${KOS_ROLE}' (expected api, worker or migrate)" >&2
        exit 64
        ;;
esac
