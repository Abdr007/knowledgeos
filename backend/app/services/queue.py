"""Redis-backed reliable job queue (§11).

Uses the ``BRPOPLPUSH`` pattern rather than ``LPOP``: the item moves atomically
from the pending list to a processing list, and is removed only when the worker
acknowledges. A worker that dies mid-job leaves the item in the processing list
where a reaper returns it after a visibility timeout, so **no job is lost to a
crash**. With a plain ``LPOP`` the item is gone the moment it is read, and every
container restart silently drops whatever was in flight.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass

from app.core.clients import get_blocking_redis, get_redis

logger = logging.getLogger(__name__)

_PENDING = "kos:v1:queue:ingest"
_PROCESSING = "kos:v1:queue:ingest:processing"
_HEARTBEAT = "kos:v1:queue:ingest:heartbeat"

#: A job unacknowledged for longer than this is assumed orphaned and requeued.
#: Generous, because embedding a large document legitimately takes minutes.
VISIBILITY_TIMEOUT_SECONDS = 900


@dataclass(frozen=True, slots=True)
class IngestJob:
    document_id: uuid.UUID
    job_id: uuid.UUID
    attempt: int = 0

    def encode(self) -> str:
        return json.dumps(
            {
                "document_id": str(self.document_id),
                "job_id": str(self.job_id),
                "attempt": self.attempt,
            }
        )

    @staticmethod
    def decode(raw: str) -> IngestJob:
        data = json.loads(raw)
        return IngestJob(
            document_id=uuid.UUID(data["document_id"]),
            job_id=uuid.UUID(data["job_id"]),
            attempt=int(data.get("attempt", 0)),
        )


def enqueue(job: IngestJob) -> None:
    get_redis().lpush(_PENDING, job.encode())
    logger.info(
        "job enqueued",
        extra={"event": "ingest.enqueued", "document_id": str(job.document_id)},
    )


def claim(timeout_seconds: int = 5) -> tuple[IngestJob, str] | None:
    """Block for a job. Returns ``(job, raw)``; ``raw`` is needed to acknowledge.

    Uses the blocking client: the shared one has a 5s socket timeout that would
    abort this call at exactly the moment it is supposed to be waiting.
    """
    raw = get_blocking_redis().brpoplpush(_PENDING, _PROCESSING, timeout=timeout_seconds)
    if raw is None:
        return None
    try:
        job = IngestJob.decode(raw)
    except Exception:
        logger.error("undecodable job discarded", extra={"raw": raw[:200]})
        get_redis().lrem(_PROCESSING, 1, raw)
        return None
    get_redis().hset(_HEARTBEAT, raw, str(int(time.time())))
    return job, raw


def acknowledge(raw: str) -> None:
    """Remove a finished job from the processing list."""
    pipe = get_redis().pipeline()
    pipe.lrem(_PROCESSING, 1, raw)
    pipe.hdel(_HEARTBEAT, raw)
    pipe.execute()


def requeue(raw: str, job: IngestJob, *, delay_seconds: int = 0) -> None:
    """Acknowledge the current attempt and re-enqueue the next one."""
    acknowledge(raw)
    if delay_seconds > 0:
        time.sleep(min(delay_seconds, 30))
    enqueue(IngestJob(document_id=job.document_id, job_id=job.job_id, attempt=job.attempt + 1))


def reap_orphans() -> int:
    """Return jobs whose worker died back to the pending list.

    Called periodically by the worker. Without it a crashed worker's job sits in
    the processing list forever — the reliable-queue pattern only pays off if
    something actually reclaims.
    """
    redis = get_redis()
    now = int(time.time())
    recovered = 0
    for raw in redis.lrange(_PROCESSING, 0, -1):
        started = redis.hget(_HEARTBEAT, raw)
        if started is None or now - int(started) > VISIBILITY_TIMEOUT_SECONDS:
            pipe = redis.pipeline()
            pipe.lrem(_PROCESSING, 1, raw)
            pipe.hdel(_HEARTBEAT, raw)
            pipe.lpush(_PENDING, raw)
            pipe.execute()
            recovered += 1
            logger.warning("reclaimed orphaned ingestion job", extra={"event": "ingest.reaped"})
    return recovered


def depth() -> dict[str, int]:
    """Queue metrics for /admin/jobs and the §26.3 signals."""
    redis = get_redis()
    return {
        "pending": int(redis.llen(_PENDING)),
        "processing": int(redis.llen(_PROCESSING)),
    }
