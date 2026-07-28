"""Provider selection (D1).

The whole point of the protocol: choosing a vendor is a lookup, not a code path
that spreads through the application. Going live on 1 August is one environment
variable.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from app.core.config import get_settings
from app.providers.llm.base import LLMProvider

logger = logging.getLogger(__name__)
settings = get_settings()


@lru_cache
def get_llm_provider() -> LLMProvider:
    provider = settings.llm_provider

    if provider == "anthropic":
        from app.providers.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider()

    if provider == "openai":
        from app.providers.llm.openai_provider import OpenAIProvider

        return OpenAIProvider()

    if provider == "scripted":
        from app.providers.llm.scripted_provider import ScriptedProvider

        logger.warning(
            "using the scripted (extractive, offline) provider — responses are "
            "quoted from sources, not generated",
            extra={"event": "llm.scripted_mode"},
        )
        return ScriptedProvider()

    raise ValueError(f"Unknown LLM_PROVIDER: {provider!r}")
