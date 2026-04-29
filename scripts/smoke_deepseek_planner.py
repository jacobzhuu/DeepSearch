from __future__ import annotations

import json
from typing import Any

from services.orchestrator.app.llm import create_llm_provider
from services.orchestrator.app.planning import ResearchPlannerError, ResearchPlannerService
from services.orchestrator.app.settings import Settings

QUERY = "What is SearXNG and how does it work?"
OPENAI_COMPATIBLE_PROVIDERS = {"openai", "openai-compatible", "openai_compatible"}


def main() -> int:
    settings = Settings()
    missing = _missing_required_settings(settings)
    if missing:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "missing_configuration",
                    "missing": missing,
                    "message": (
                        "Set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL, and "
                        "LLM_PROVIDER=openai_compatible in .env before running this smoke test."
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    provider = create_llm_provider(settings)
    planner = ResearchPlannerService(
        provider=provider,
        model=settings.llm_model,
        max_output_tokens=settings.llm_max_output_tokens,
        max_subquestions=settings.research_planner_max_subquestions,
        max_search_queries=settings.research_planner_max_search_queries,
    )

    try:
        plan = planner.plan(task_id=_zero_uuid(), query=QUERY, constraints={})
    except ResearchPlannerError as error:
        print(
            json.dumps(
                {
                    "ok": False,
                    "provider": _provider_name(settings),
                    "model": settings.llm_model,
                    "error": _sanitize_payload(error.to_payload(), settings.llm_api_key),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "ok": True,
                "provider": _provider_name(settings),
                "model": settings.llm_model,
                "intent": plan.intent,
                "subquestions": plan.subquestions,
                "search_queries": [query.to_payload() for query in plan.search_queries],
                "warnings": plan.warnings,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _missing_required_settings(settings: Settings) -> list[str]:
    missing: list[str] = []
    provider = _provider_name(settings)
    if provider not in OPENAI_COMPATIBLE_PROVIDERS:
        missing.append("LLM_PROVIDER=openai_compatible")
    if not settings.llm_base_url.strip():
        missing.append("LLM_BASE_URL")
    if not settings.llm_api_key.strip():
        missing.append("LLM_API_KEY")
    if not settings.llm_model.strip():
        missing.append("LLM_MODEL")
    return missing


def _provider_name(settings: Settings) -> str:
    return settings.llm_provider.strip().lower() or "noop"


def _zero_uuid() -> Any:
    from uuid import UUID

    return UUID("00000000-0000-0000-0000-000000000000")


def _sanitize_payload(value: Any, api_key: str) -> Any:
    if isinstance(value, str):
        return value.replace(api_key, "[redacted]") if api_key else value
    if isinstance(value, list):
        return [_sanitize_payload(item, api_key) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_payload(item, api_key) for key, item in value.items()}
    return value


if __name__ == "__main__":
    raise SystemExit(main())
