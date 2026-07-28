"""Ingestion worker entrypoint (D6, §11).

Runs the same image as the API with a different command, so the two cannot drift
apart. Scales independently: chat load scales the API, a bulk import scales
workers. Coupling them — ingesting inside the request — means a hundred-document
upload degrades chat latency for everyone.

    python -m app.worker
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
import time
from types import FrameType

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.models.content import Document, IngestionJob
from app.db.models.enums import DocumentStatus, JobStatus
from app.db.session import SessionLocal
from app.providers.vector.registry import get_vector_store
from app.services import queue
from app.services.ingestion_pipeline import process_document

settings = get_settings()
configure_logging(level=settings.log_level, json_output=settings.log_json)
logger = logging.getLogger("app.worker")

MAX_ATTEMPTS = 3
REAP_INTERVAL_SECONDS = 60

_shutdown = False


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    """Finish the job in hand, then stop.

    Dropping a half-embedded document on SIGTERM would leave it PROCESSING
    forever with no worker to pick it up.
    """
    global _shutdown
    logger.info("shutdown signal received; finishing current job", extra={"signal": signum})
    _shutdown = True


def run() -> int:
    """Entrypoint for the standalone worker process."""
    # Signal handlers can only be installed from the main thread, which is why
    # this is separate from consume_forever below.
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    return consume_forever()


def start_inline(stop: threading.Event) -> threading.Thread:
    """Run the consume loop in a background thread inside the API process.

    A DEPLOYMENT TOPOLOGY, not an architecture change. The queue, the reliable
    claim/acknowledge protocol and the retry semantics are identical; the loop
    simply shares a process with the API instead of owning one.

    It exists because several platforms' free tiers offer web services and no
    background workers, and a demo that cannot ingest is not a demo. Anywhere
    that can run a second process should run `python -m app.worker` instead and
    scale the two independently (D6) — embedding is CPU-bound, so sharing a
    process means a bulk import competes with chat for the same interpreter.
    """
    thread = threading.Thread(
        target=consume_forever, kwargs={"stop": stop}, name="ingest-worker", daemon=True
    )
    thread.start()
    logger.warning(
        "ingestion worker running INSIDE the API process - single-node topology",
        extra={"event": "worker.inline"},
    )
    return thread


def consume_forever(stop: threading.Event | None = None) -> int:
    logger.info(
        "worker starting",
        extra={
            "embedding_model": settings.embedding_model,
            "embedding_provider": settings.embedding_provider,
        },
    )

    # Fail fast on a mis-sized collection rather than at the first upsert.
    try:
        get_vector_store().ensure_collection()
    except Exception:
        logger.exception("could not prepare the vector collection")
        return 1

    last_reap = 0.0

    while not _shutdown and not (stop is not None and stop.is_set()):
        now = time.monotonic()
        if now - last_reap > REAP_INTERVAL_SECONDS:
            try:
                recovered = queue.reap_orphans()
                if recovered:
                    logger.warning("requeued orphaned jobs", extra={"count": recovered})
            except Exception:
                logger.exception("reaper failed")
            last_reap = now

        try:
            claimed = queue.claim(timeout_seconds=5)
        except Exception:
            logger.exception("failed to claim a job; backing off")
            time.sleep(2)
            continue

        if claimed is None:
            continue

        job, raw = claimed
        db = SessionLocal()
        try:
            # POISON-PILL GUARD.
            #
            # The queue payload carries its own attempt counter, but the reaper
            # re-enqueues the ORIGINAL payload when a worker dies without
            # acknowledging — so a job that kills its worker (an OOM on a large
            # document, say) comes back with attempt=0 every time and is retried
            # forever, taking the service down on each pass. The exception-based
            # retry below never runs, because a killed process catches nothing.
            #
            # The durable attempt count lives on the job row, incremented and
            # committed BEFORE the risky work starts, so it survives a kill. This
            # is the only place that can see it.
            persisted = db.get(IngestionJob, job.job_id) if job.job_id else None
            if persisted is not None and persisted.attempts >= MAX_ATTEMPTS:
                logger.error(
                    "abandoning job that repeatedly killed its worker",
                    extra={
                        "event": "ingest.poison_pill",
                        "document_id": str(job.document_id),
                        "attempts": persisted.attempts,
                    },
                )
                document = db.get(Document, job.document_id)
                if document is not None:
                    document.status = DocumentStatus.FAILED
                    document.error_message = (
                        f"Processing failed {persisted.attempts} times without completing. "
                        "The document is most likely too large for this instance's memory. "
                        "Try a smaller file, or run the full stack where the worker has its "
                        "own process."
                    )
                persisted.status = JobStatus.FAILED
                persisted.last_error = (
                    "worker terminated repeatedly (suspected resource exhaustion)"
                )
                db.commit()
                queue.acknowledge(raw)
                continue

            process_document(db, document_id=job.document_id, job_id=job.job_id)
            queue.acknowledge(raw)
        except Exception as exc:
            if job.attempt + 1 < MAX_ATTEMPTS:
                # Exponential backoff. A transient failure (a blip reaching
                # Qdrant) deserves another try; a malformed PDF does not improve
                # with repetition, which is what the attempt cap is for.
                delay = 2 ** (job.attempt + 1)
                logger.warning(
                    "ingestion attempt failed; requeueing",
                    extra={
                        "document_id": str(job.document_id),
                        "attempt": job.attempt + 1,
                        "delay_seconds": delay,
                        "error": str(exc)[:300],
                    },
                )
                queue.requeue(raw, job, delay_seconds=delay)
            else:
                logger.error(
                    "ingestion permanently failed",
                    extra={
                        "event": "ingest.exhausted",
                        "document_id": str(job.document_id),
                        "attempts": job.attempt + 1,
                    },
                )
                queue.acknowledge(raw)
        finally:
            db.close()

    logger.info("worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(run())
