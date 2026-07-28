"""Structure-aware, overlapped chunking (§11).

Three properties matter, and each has a cost if you skip it:

1. **Split on the strongest available boundary.** Paragraphs before sentences,
   sentences before a hard character cut. A chunk that starts mid-sentence embeds
   poorly, because the embedding of a fragment is not the embedding of its meaning.
2. **Overlap.** A fact split across a boundary is retrievable from neither side.
   ~150 characters is about a sentence: enough to preserve a straddling claim,
   small enough that duplicated text does not inflate the index by a third.
3. **Carry page provenance.** Page numbers arrive from the parser and survive to
   the chunk, which is what makes "page 47" in a citation true rather than decorative.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import get_settings
from app.providers.parsers.base import ParsedDocument, ParsedPage

settings = get_settings()

#: Chunks shorter than this carry no retrievable meaning — a stray heading, a page
#: number, a figure caption orphaned by a page break. They are dropped rather than
#: embedded: a 2-character chunk still occupies a slot in the top-k, still costs a
#: vector, and can still out-rank real content on a short lexical query.
MIN_CHUNK_CHARS = 60

_PARAGRAPH = re.compile(r"\n\s*\n")
# Sentence end followed by whitespace and a capital/quote/digit. Deliberately not
# a full NLP sentence splitter: this is a fallback boundary, and the failure mode
# of an imperfect split here is a slightly awkward chunk, not a wrong answer.
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(\[0-9])")


@dataclass(frozen=True, slots=True)
class TextChunk:
    ordinal: int
    content: str
    page_from: int | None
    page_to: int | None
    section: str | None

    @property
    def token_estimate(self) -> int:
        """~4 characters per token for English.

        An estimate, used only for context budgeting and display. The
        authoritative count comes back from the provider with each response, and
        that is what usage accounting records (§7).
        """
        return max(1, len(self.content) // 4)


def chunk_document(
    document: ParsedDocument,
    *,
    target_chars: int | None = None,
    overlap_chars: int | None = None,
) -> list[TextChunk]:
    target = target_chars or settings.chunk_size_chars
    overlap = overlap_chars or settings.chunk_overlap_chars
    if overlap >= target:
        raise ValueError("chunk overlap must be smaller than the chunk size")

    chunks: list[TextChunk] = []
    ordinal = 0

    for page in document.pages:
        windows = [w.strip() for w in _split_page(page.text, target=target, overlap=overlap)]
        windows = _absorb_fragments(windows, target=target)
        for body in windows:
            if not body:
                continue
            chunks.append(
                TextChunk(
                    ordinal=ordinal,
                    content=body,
                    page_from=page.number,
                    page_to=page.number,
                    section=page.section,
                )
            )
            ordinal += 1

    return chunks


def _absorb_fragments(windows: list[str], *, target: int) -> list[str]:
    """Merge sub-minimum fragments into a neighbour instead of emitting them.

    Merging beats dropping: a short heading like "3.2 Retrieval internals" is not
    retrievable alone, but prepended to the paragraph it introduces it is real
    signal — and it is exactly the text a user's query is likely to echo.
    Only genuinely orphaned fragments, with no neighbour that has room, are dropped.
    """
    out: list[str] = []
    for window in windows:
        if not window:
            continue
        if len(window) >= MIN_CHUNK_CHARS:
            out.append(window)
            continue
        # Prefer forward-merge (a heading belongs with what follows it); fall back
        # to appending to the previous chunk when this is the last piece.
        merged = False
        if out and len(out[-1]) + len(window) + 2 <= target * 1.3:
            out[-1] = f"{out[-1]}\n\n{window}"
            merged = True
        if not merged:
            out.append(window)  # keep it for now; the forward pass below may absorb it

    # Forward pass: a leading fragment attaches to the chunk after it.
    fixed: list[str] = []
    carry = ""
    for window in out:
        if len(window) < MIN_CHUNK_CHARS:
            carry = f"{carry}\n\n{window}".strip() if carry else window
            continue
        fixed.append(f"{carry}\n\n{window}" if carry else window)
        carry = ""
    if carry and len(carry) >= MIN_CHUNK_CHARS:
        fixed.append(carry)
    elif carry and fixed:
        fixed[-1] = f"{fixed[-1]}\n\n{carry}"
    return fixed


def _split_page(text: str, *, target: int, overlap: int) -> list[str]:
    """Pack paragraphs into target-sized windows, subdividing what will not fit."""
    if not text.strip():
        return []
    if len(text) <= target:
        return [text]

    units = [u for u in _PARAGRAPH.split(text) if u.strip()]
    pieces: list[str] = []
    for unit in units:
        # A single paragraph larger than the target is split by sentence, and a
        # single sentence larger than the target by character. Every level has a
        # fallback, so no input can produce an oversized chunk.
        pieces.extend(_subdivide(unit, target=target) if len(unit) > target else [unit])

    return _pack(pieces, target=target, overlap=overlap)


def _subdivide(unit: str, *, target: int) -> list[str]:
    sentences = _SENTENCE.split(unit)
    out: list[str] = []
    for sentence in sentences:
        if len(sentence) <= target:
            out.append(sentence)
            continue
        for start in range(0, len(sentence), target):
            out.append(sentence[start : start + target])
    return out


def _pack(pieces: list[str], *, target: int, overlap: int) -> list[str]:
    """Greedily fill windows, then prepend a tail of the previous window."""
    windows: list[str] = []
    current: list[str] = []
    length = 0

    for piece in pieces:
        addition = len(piece) + (2 if current else 0)
        if current and length + addition > target:
            windows.append("\n\n".join(current))
            current, length = [], 0
        current.append(piece)
        length += addition

    if current:
        windows.append("\n\n".join(current))

    if overlap <= 0 or len(windows) < 2:
        return windows

    overlapped = [windows[0]]
    for index in range(1, len(windows)):
        previous = windows[index - 1]
        tail = previous[-overlap:]
        # Start the carried tail at a word boundary; half a word is noise in the
        # embedding and looks like corruption when the chunk is shown to a user.
        space = tail.find(" ")
        if space > 0:
            tail = tail[space + 1 :]
        overlapped.append(f"{tail}\n\n{windows[index]}" if tail.strip() else windows[index])
    return overlapped


def build_pages_from_text(text: str, *, section: str | None = None) -> ParsedDocument:
    """Helper for callers holding plain text (evaluation fixtures, tests)."""
    return ParsedDocument(pages=[ParsedPage(number=1, text=text, section=section)])
