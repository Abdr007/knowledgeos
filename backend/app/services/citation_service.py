"""Citation extraction and validation (D9, §10).

Citations are **verified, not trusted**. The model is asked to emit [n] markers;
this module checks each one against the sources actually supplied and strips any
that do not resolve.

Why it matters: a model that cites [7] when six sources were given has produced a
citation that looks authoritative and points at nothing. That is worse than no
citation — it manufactures the appearance of grounding. The stripped-marker rate
is tracked as a quality signal (§26.3), because a rise in it is an early sign of
a prompt or model regression.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.services.retrieval_service import RetrievedChunk

logger = logging.getLogger(__name__)

# [1] or [1, 3] or [1][2]. Deliberately does not match [word] so ordinary
# bracketed prose is left alone.
_MARKER = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")


@dataclass(frozen=True, slots=True)
class ValidatedCitation:
    marker: int
    chunk: RetrievedChunk

    @property
    def snippet(self) -> str:
        text = " ".join(self.chunk.content.split())
        return text[:400] + ("…" if len(text) > 400 else "")


@dataclass(frozen=True, slots=True)
class CitationResult:
    text: str
    citations: list[ValidatedCitation]
    validated_markers: list[int]
    stripped_markers: list[int]

    @property
    def citation_rate(self) -> float:
        total = len(self.validated_markers) + len(self.stripped_markers)
        return len(self.validated_markers) / total if total else 1.0


def validate(text: str, sources: list[RetrievedChunk]) -> CitationResult:
    """Strip unresolvable markers and return the citations that survived."""
    valid_range = range(1, len(sources) + 1)
    seen: dict[int, ValidatedCitation] = {}
    stripped: set[int] = set()

    def _replace(match: re.Match[str]) -> str:
        numbers = [int(n.strip()) for n in match.group(1).split(",")]
        kept: list[int] = []
        for number in numbers:
            if number in valid_range:
                kept.append(number)
                if number not in seen:
                    seen[number] = ValidatedCitation(marker=number, chunk=sources[number - 1])
            else:
                stripped.add(number)
        if not kept:
            return ""  # remove the marker entirely rather than leave a dangling []
        return "".join(f"[{n}]" for n in kept)

    cleaned = _MARKER.sub(_replace, text)
    # Removing a marker can leave " ." or a double space behind.
    cleaned = re.sub(r"\s+([.,;:])", r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)

    if stripped:
        logger.warning(
            "stripped unresolvable citation markers",
            extra={
                "event": "chat.citations_stripped",
                "stripped": sorted(stripped),
                "source_count": len(sources),
            },
        )

    return CitationResult(
        text=cleaned.strip(),
        citations=[seen[m] for m in sorted(seen)],
        validated_markers=sorted(seen),
        stripped_markers=sorted(stripped),
    )


def heuristic_groundedness(text: str, sources: list[RetrievedChunk]) -> float:
    """Cheap lexical support score, used when no judge model is available.

    Measures what fraction of the answer's content words appear in the sources.
    It is a floor, not a truth: high lexical overlap does not prove a claim is
    correctly attributed. It exists so the metric is always populated, and it is
    labelled as heuristic wherever it is surfaced.
    """
    if not text or not sources:
        return 0.0
    corpus = " ".join(c.content for c in sources).lower()
    corpus_words = set(re.findall(r"\w+", corpus))
    answer_words = [w for w in re.findall(r"\w+", text.lower()) if len(w) > 3]
    if not answer_words:
        return 0.0
    supported = sum(1 for w in answer_words if w in corpus_words)
    return round(supported / len(answer_words), 4)
