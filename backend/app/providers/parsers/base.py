"""Document parsing protocol (§11, §29.1).

Every parser returns the same ``ParsedDocument``, so the ingestion pipeline
cannot tell which one ran (Liskov, §6).

Parsers return **pages, not a flat string**. Page structure is what lets a
citation say "page 47" and deep-link there, and it cannot be recovered after
concatenation — so it is carried from the very first step rather than
reconstructed later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from app.db.models.enums import SourceType


@dataclass(frozen=True, slots=True)
class ParsedPage:
    """One page, slide, or logical section of a document."""

    number: int
    text: str
    #: Nearest enclosing heading, when the format exposes one. Becomes chunk
    #: metadata and is shown in citations.
    section: str | None = None


@dataclass(slots=True)
class ParsedDocument:
    pages: list[ParsedPage] = field(default_factory=list)
    title: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def total_characters(self) -> int:
        return sum(len(p.text) for p in self.pages)

    @property
    def is_empty(self) -> bool:
        """True when parsing produced no usable text.

        Common for scanned PDFs, which are images of text. Treated as a hard
        failure with a clear message rather than an empty document that silently
        contributes nothing to retrieval — a document that ingests "successfully"
        and then never appears in an answer is far harder to diagnose.
        """
        return self.total_characters < 20


@runtime_checkable
class DocumentParser(Protocol):
    source_type: SourceType

    def parse(self, data: bytes, *, filename: str | None = None) -> ParsedDocument: ...
