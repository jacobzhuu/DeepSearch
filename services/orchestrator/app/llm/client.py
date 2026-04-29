from __future__ import annotations

import httpx

from services.orchestrator.app.llm.providers import (
    LLMProvider,
    NoopLLMProvider,
    OpenAICompatibleLLMProvider,
    UnsupportedLLMProvider,
)
from services.orchestrator.app.settings import Settings


def create_llm_provider(
    settings: Settings,
    *,
    client: httpx.Client | None = None,
) -> LLMProvider:
    normalized_provider = settings.llm_provider.strip().lower() or "noop"
    if normalized_provider == "noop":
        return NoopLLMProvider()
    if normalized_provider in {"openai", "openai-compatible", "openai_compatible"}:
        return OpenAICompatibleLLMProvider(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
            client=client,
        )
    return UnsupportedLLMProvider(settings.llm_provider.strip())
