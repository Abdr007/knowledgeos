"""Redis sliding-window rate limiting (§14).

A fixed-window counter allows a double-rate burst across a window boundary: 20
requests at 11:59:59 and 20 more at 12:00:00 both pass a "20 per minute" check.
A sorted set keyed by timestamp gives a true sliding window at the cost of one
round trip.

The whole check is a single pipelined transaction so concurrent requests cannot
interleave between the prune, the count and the insert.
"""

from __future__ import annotations

import logging
import time

from app.core.clients import get_redis
from app.core.errors import RateLimitError

logger = logging.getLogger(__name__)

_PREFIX = "kos:v1:rl"


def check_rate_limit(
    identity: str,
    *,
    action: str,
    limit: int,
    window_seconds: int = 60,
) -> None:
    """Raise RateLimitError when ``identity`` exceeds ``limit`` in the window.

    Fails **open**. If Redis is unavailable the request proceeds: an outage in
    the limiter should degrade protection, not availability. The exception is
    the auth denylist (§14), which fails closed and is checked elsewhere.
    """
    key = f"{_PREFIX}:{action}:{identity}"
    now_ms = int(time.time() * 1000)
    window_ms = window_seconds * 1000
    member = f"{now_ms}-{time.perf_counter_ns()}"  # unique per call

    try:
        pipe = get_redis().pipeline(transaction=True)
        pipe.zremrangebyscore(key, 0, now_ms - window_ms)
        pipe.zcard(key)
        pipe.zadd(key, {member: now_ms})
        # Expire slightly beyond the window so an idle key cannot linger forever.
        pipe.expire(key, window_seconds + 1)
        _, count, _, _ = pipe.execute()
    except Exception:
        logger.warning("rate limiter unavailable; allowing request", extra={"action": action})
        return

    if int(count) >= limit:
        retry_after = window_seconds
        logger.info(
            "rate limit exceeded",
            extra={"action": action, "limit": limit, "window_seconds": window_seconds},
        )
        raise RateLimitError(
            f"Too many requests. Limit is {limit} per {window_seconds} seconds.",
            retry_after=retry_after,
        )


def reset_rate_limit(identity: str, *, action: str) -> None:
    """Clear a counter. Used by tests and by admin unblocking."""
    try:
        get_redis().delete(f"{_PREFIX}:{action}:{identity}")
    except Exception:
        logger.warning("failed to reset rate limit", extra={"action": action})
