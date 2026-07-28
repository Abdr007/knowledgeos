"""Usage and cost accounting (§7, §11).

Every provider call — chat, embedding, titling — writes one row. Recording
embeddings alongside chat is what makes reported cost reconcile against a
provider invoice instead of counting only the visible half.

Cost is computed at write time and stored. Deriving it on read would let a price
change silently restate historical spend, which makes month-over-month
comparisons meaningless.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.models.enums import UsageKind
from app.db.models.ops import UsageEvent

logger = logging.getLogger(__name__)

#: USD per 1M tokens, (input, output). Pinned per model version, because a
#: floating alias would change both behaviour and price without a deploy (§27.2).
#: Local embeddings are genuinely free at the margin — the cost is CPU time,
#: which is captured as latency rather than invented as a dollar figure.
_PRICE_PER_MILLION: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5": (Decimal("15.00"), Decimal("75.00")),
    "claude-sonnet-5": (Decimal("3.00"), Decimal("15.00")),
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
    "gpt-4.1": (Decimal("2.00"), Decimal("8.00")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "text-embedding-3-small": (Decimal("0.02"), Decimal("0")),
    "BAAI/bge-small-en-v1.5": (Decimal("0"), Decimal("0")),
    "scripted": (Decimal("0"), Decimal("0")),
}

_MILLION = Decimal(1_000_000)


def estimate_cost_usd(model: str, *, input_tokens: int, output_tokens: int) -> Decimal:
    prices = _PRICE_PER_MILLION.get(model)
    if prices is None:
        # An unknown model costs 0 rather than guessing. A wrong number in a
        # billing dashboard is worse than a visibly missing one, and the log line
        # is what prompts someone to add the price.
        logger.warning("no price table entry for model", extra={"model": model})
        return Decimal(0)
    input_price, output_price = prices
    cost = (
        Decimal(input_tokens) * input_price + Decimal(output_tokens) * output_price
    ) / _MILLION
    return cost.quantize(Decimal("0.000001"))


def record_usage(
    db: Session,
    *,
    org_id: uuid.UUID,
    kind: UsageKind,
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    workspace_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    latency_ms: int | None = None,
) -> UsageEvent:
    event = UsageEvent(
        org_id=org_id,
        workspace_id=workspace_id,
        user_id=user_id,
        kind=kind,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=estimate_cost_usd(
            model, input_tokens=input_tokens, output_tokens=output_tokens
        ),
        latency_ms=latency_ms,
    )
    db.add(event)
    db.flush()
    return event
