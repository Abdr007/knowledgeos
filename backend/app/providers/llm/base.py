"""LLM protocol (D1, §29.1).

Narrow on purpose (Interface Segregation, §6): generation only. Embeddings are a
separate protocol because Anthropic implements one and not the other, and a fat
"AIProvider" would force a NotImplementedError on a legal configuration.

Streaming is modelled as typed events rather than a bare string iterator so usage
and finish reason arrive as structured data instead of being inferred from where
the text stopped.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ChatTurn:
    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True, slots=True)
class TextDelta:
    text: str


@dataclass(frozen=True, slots=True)
class StreamUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class StreamDone:
    finish_reason: Literal["stop", "length", "error"]


StreamEvent = TextDelta | StreamUsage | StreamDone


@dataclass(frozen=True, slots=True)
class Completion:
    text: str
    input_tokens: int
    output_tokens: int


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    def stream_chat(
        self,
        *,
        system: str,
        turns: list[ChatTurn],
        max_tokens: int = 1500,
        temperature: float = 0.2,
    ) -> AsyncIterator[StreamEvent]: ...

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 300,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> Completion: ...
