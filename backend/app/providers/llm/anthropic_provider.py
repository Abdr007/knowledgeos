"""Anthropic provider (D1 default)."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from app.core.config import get_settings
from app.core.errors import ProviderError, ProviderTimeoutError
from app.providers.llm.base import ChatTurn, Completion, StreamDone, StreamEvent, StreamUsage, TextDelta

logger = logging.getLogger(__name__)
settings = get_settings()


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or settings.chat_model
        self._api_key = api_key or settings.anthropic_api_key
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            if not self._api_key:
                raise ProviderError(
                    "ANTHROPIC_API_KEY is not configured.", retryable=False
                )
            from anthropic import AsyncAnthropic

            self._client = AsyncAnthropic(
                api_key=self._api_key,
                timeout=settings.llm_request_timeout_seconds,
                max_retries=settings.llm_max_retries,
            )
        return self._client

    async def stream_chat(
        self,
        *,
        system: str,
        turns: list[ChatTurn],
        max_tokens: int = 1500,
        temperature: float = 0.2,
    ) -> AsyncIterator[StreamEvent]:
        import anthropic

        client = self._ensure_client()
        messages = [{"role": t.role, "content": t.content} for t in turns]

        try:
            async with client.messages.stream(
                model=self.model,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            ) as stream:
                async for text in stream.text_stream:
                    if text:
                        yield TextDelta(text)
                final = await stream.get_final_message()
                yield StreamUsage(
                    input_tokens=final.usage.input_tokens,
                    output_tokens=final.usage.output_tokens,
                )
                yield StreamDone(
                    finish_reason="length" if final.stop_reason == "max_tokens" else "stop"
                )
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeoutError(f"The model provider timed out: {exc}") from exc
        except anthropic.APIStatusError as exc:
            # 4xx other than 429 is our bug (bad request, bad key) and retrying
            # cannot help; 429 and 5xx are worth another attempt.
            retryable = exc.status_code == 429 or exc.status_code >= 500
            raise ProviderError(
                f"The model provider returned {exc.status_code}.", retryable=retryable
            ) from exc
        except anthropic.APIError as exc:
            raise ProviderError(f"The model provider failed: {exc}") from exc

    async def complete(
        self,
        *,
        system: str,
        prompt: str,
        max_tokens: int = 300,
        temperature: float = 0.0,
        model: str | None = None,
    ) -> Completion:
        import anthropic

        client = self._ensure_client()
        try:
            response = await client.messages.create(
                model=model or settings.utility_model,
                system=system,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except anthropic.APIError as exc:
            raise ProviderError(f"The model provider failed: {exc}") from exc

        text = "".join(block.text for block in response.content if block.type == "text")
        return Completion(
            text=text,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
