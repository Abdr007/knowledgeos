"""OpenAI provider.

Present because the blueprint specifies OpenAI (D1). The default is Anthropic
only because that is the credential this deployment holds — both are complete,
and `LLM_PROVIDER` chooses.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from app.core.config import get_settings
from app.core.errors import ProviderError, ProviderTimeoutError
from app.providers.llm.base import (
    ChatTurn,
    Completion,
    StreamDone,
    StreamEvent,
    StreamUsage,
    TextDelta,
)

logger = logging.getLogger(__name__)
settings = get_settings()


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or settings.chat_model
        self._api_key = api_key or settings.openai_api_key
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            if not self._api_key:
                raise ProviderError("OPENAI_API_KEY is not configured.", retryable=False)
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
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
        import openai

        client = self._ensure_client()
        messages = [{"role": "system", "content": system}]
        messages += [{"role": t.role, "content": t.content} for t in turns]

        try:
            stream = await client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                stream=True,
                # Without this the usage block is omitted from streamed
                # responses, and cost accounting silently reports zero.
                stream_options={"include_usage": True},
            )
            finish = "stop"
            async for event in stream:
                if event.choices:
                    choice = event.choices[0]
                    if choice.delta and choice.delta.content:
                        yield TextDelta(choice.delta.content)
                    if choice.finish_reason:
                        finish = "length" if choice.finish_reason == "length" else "stop"
                if event.usage is not None:
                    yield StreamUsage(
                        input_tokens=event.usage.prompt_tokens,
                        output_tokens=event.usage.completion_tokens,
                    )
            yield StreamDone(finish_reason=finish)
        except openai.APITimeoutError as exc:
            raise ProviderTimeoutError(f"The model provider timed out: {exc}") from exc
        except openai.APIStatusError as exc:
            retryable = exc.status_code == 429 or exc.status_code >= 500
            raise ProviderError(
                f"The model provider returned {exc.status_code}.", retryable=retryable
            ) from exc
        except openai.APIError as exc:
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
        import openai

        client = self._ensure_client()
        try:
            response = await client.chat.completions.create(
                model=model or settings.utility_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=max_tokens,
                temperature=temperature,
            )
        except openai.APIError as exc:
            raise ProviderError(f"The model provider failed: {exc}") from exc

        return Completion(
            text=response.choices[0].message.content or "",
            input_tokens=response.usage.prompt_tokens if response.usage else 0,
            output_tokens=response.usage.completion_tokens if response.usage else 0,
        )
