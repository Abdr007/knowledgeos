"""Deterministic offline provider (D1).

**What this is, stated plainly:** not a language model. It is an *extractive*
responder that selects the most query-relevant sentences from the supplied
sources and streams them back with correct citation markers. It generates no
new prose and infers nothing.

**Why it exists.** Two reasons, and the second is the important one:

1. The live API credential arrives on 1 August. Everything except generation is
   already buildable and testable because embeddings run locally (D2); this keeps
   the *last* piece testable too, so Milestone 8 ships verified rather than
   assumed.
2. The test suite must be deterministic, free and offline. Asserting on a real
   model's output means asserting on something that legitimately varies between
   runs, so the tests either become flaky or become so loose they check nothing.

Because it is extractive, every sentence it emits is by construction present in
the sources — which makes it the ideal fixture for testing the citation validator
and the groundedness scorer, whose whole job is detecting text that ISN'T.

The UI labels responses produced this way. Presenting extraction as generation
would be a lie to whoever is reading the demo.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator

from app.providers.llm.base import (
    ChatTurn,
    Completion,
    StreamDone,
    StreamEvent,
    StreamUsage,
    TextDelta,
)

logger = logging.getLogger(__name__)

_SOURCE_BLOCK = re.compile(r'<source id="(\d+)"[^>]*>(.*?)</source>', re.DOTALL)
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[0-9])")
_WORD = re.compile(r"\w+")

_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "our",
        "that",
        "the",
        "their",
        "there",
        "these",
        "they",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    ]
)


class ScriptedProvider:
    """Protocol-conformant, deterministic, no network."""

    name = "scripted"

    def __init__(self, model: str = "scripted", delay_seconds: float = 0.012) -> None:
        self.model = model
        # A small per-token delay so the SSE pipeline, the client's incremental
        # rendering and cancellation are all exercised realistically. Streaming
        # that completes instantly tests none of them.
        self._delay = delay_seconds

    async def stream_chat(
        self,
        *,
        system: str,
        turns: list[ChatTurn],
        max_tokens: int = 1500,
        temperature: float = 0.2,
    ) -> AsyncIterator[StreamEvent]:
        prompt = turns[-1].content if turns else ""
        question = _extract_question(prompt)
        sources = _SOURCE_BLOCK.findall(prompt)

        answer = _compose(question, sources)

        # Stream in word-sized pieces, the granularity a real provider emits.
        for token in re.findall(r"\S+\s*", answer):
            yield TextDelta(token)
            if self._delay:
                await asyncio.sleep(self._delay)

        yield StreamUsage(
            input_tokens=_estimate_tokens(system) + _estimate_tokens(prompt),
            output_tokens=_estimate_tokens(answer),
        )
        yield StreamDone(finish_reason="stop")

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 300,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> Completion:
        # Used for conversation titling and the groundedness pass. Deterministic
        # and cheap; the real providers do the same work with a small model.
        text = _title_from(prompt)
        return Completion(
            text=text,
            input_tokens=_estimate_tokens(prompt),
            output_tokens=_estimate_tokens(text),
        )


# ── composition ──────────────────────────────────────────────────────────


def _extract_question(prompt: str) -> str:
    marker = "Question:"
    if marker in prompt:
        return prompt.rsplit(marker, 1)[-1].strip()
    return prompt.strip()[-500:]


def _significant(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) > 2 and w not in _STOPWORDS}


def _compose(question: str, sources: list[tuple[str, str]]) -> str:
    if not sources:
        return (
            "I could not find anything in this workspace that answers that question. "
            "Try uploading a document that covers it, or rephrasing the question."
        )

    query_terms = _significant(question)

    # Score every sentence of every source by overlap with the question, keeping
    # the source id so the citation marker is correct rather than decorative.
    scored: list[tuple[float, int, str]] = []
    for source_id, body in sources:
        for sentence in _SENTENCE.split(body.strip()):
            sentence = " ".join(sentence.split())
            if len(sentence) < 40:
                continue
            terms = _significant(sentence)
            if not terms:
                continue
            overlap = len(query_terms & terms)
            if overlap == 0:
                continue
            # Normalise by length so a long paragraph does not win purely by
            # containing more words.
            score = overlap / (len(terms) ** 0.5)
            scored.append((score, int(source_id), sentence))

    scored.sort(key=lambda t: (-t[0], t[1]))

    if not scored:
        cited = ", ".join(f"[{sid}]" for sid, _ in sources[:3])
        return (
            "The sources retrieved for this question do not appear to address it directly. "
            f"The closest material is in {cited}, but none of it answers what you asked."
        )

    selected = scored[:4]
    # Restore source order so the answer reads in document order rather than
    # score order, which is how a person would actually write it up.
    selected.sort(key=lambda t: t[1])

    parts = [f"{sentence} [{source_id}]" for _score, source_id, sentence in selected]
    body = " ".join(parts)

    return (
        f"{body}\n\n"
        "_(Extractive answer: these sentences are quoted verbatim from the cited sources. "
        "Set `LLM_PROVIDER=anthropic` for generated prose.)_"
    )


def _title_from(prompt: str) -> str:
    text = prompt.strip().split("\n")[-1]
    words = [w for w in _WORD.findall(text) if w.lower() not in _STOPWORDS][:6]
    return " ".join(w.capitalize() for w in words) or "New conversation"


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)
