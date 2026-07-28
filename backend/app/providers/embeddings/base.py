"""Embedding protocol (§29.1).

Deliberately a *separate* protocol from LLMProvider, not a method on a shared
"AIProvider" (Interface Segregation, §6). Anthropic implements chat and does not
implement embeddings; a fat interface would force a NotImplementedError on a
configuration that is entirely legal.

``kind`` distinguishes document text from queries because asymmetric models —
including the BGE family used here — expect a prefix on one side and not the
other. Embedding a query as if it were a document measurably degrades recall,
and it is an invisible bug: everything works, the answers are just worse.
"""

from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

EmbedKind = Literal["document", "query"]


@runtime_checkable
class EmbeddingProvider(Protocol):
    model_name: str
    dimensions: int

    def embed(self, texts: list[str], *, kind: EmbedKind = "document") -> list[list[float]]: ...
