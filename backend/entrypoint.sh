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

# Apply migrations before starting, when explicitly asked.
#
# TDD §19 says migrations run as a SEPARATE pre-deploy job, never at application
# start, because N replicas booting together race Alembic. That reasoning is
# unchanged — this is opt-in and safe only at a single replica, which is what a
# free tier gives you and what MIGRATE_ON_START is documented to assume.
# Anywhere with more than one instance must run `KOS_ROLE=migrate` as its own
# step and leave this unset.
if [ "${MIGRATE_ON_START:-false}" = "true" ]; then
    echo "MIGRATE_ON_START=true - applying migrations (single-replica deployments only)"
    alembic upgrade head
fi

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
