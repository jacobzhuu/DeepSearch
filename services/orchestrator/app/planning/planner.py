from __future__ import annotations

import json
import re
from json import JSONDecodeError
from typing import Any
from uuid import UUID

from services.orchestrator.app.llm import LLMError, LLMProvider, LLMRequest, create_llm_provider
from services.orchestrator.app.llm.providers import NoopLLMProvider
from services.orchestrator.app.planning.types import PlannedSearchQuery, ResearchPlan
from services.orchestrator.app.research_quality import answer_slots_for_query
from services.orchestrator.app.settings import Settings

SYSTEM_PROMPT = """You plan evidence-first web research.
Return one JSON object only. Do not use markdown fences, explanations, claims, or final answers."""

USER_PROMPT_TEMPLATE = """Create a bounded research plan for this query.

Query:
{query}

Return a JSON object with exactly these top-level keys:
intent, normalized_question, subquestions, search_queries, source_preferences,
answer_outline, risk_notes, planner_mode, warnings.

Use planner_mode = "llm".

Each search_queries item must include query_text, rationale, expected_source_type, priority.
source_preferences must include preferred_domains, avoid_domains, and freshness_required.
Prefer official and reference sources when possible. Return JSON only."""


class ResearchPlannerError(RuntimeError):
    def __init__(
        self,
        *,
        message: str,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "message": str(self),
            "details": self.details,
        }


class ResearchPlannerService:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        model: str,
        max_output_tokens: int,
        max_subquestions: int,
        max_search_queries: int,
    ) -> None:
        self.provider = provider
        self.model = model
        self.max_output_tokens = max_output_tokens
        self.max_subquestions = max(1, max_subquestions)
        self.max_search_queries = max(1, max_search_queries)

    def plan(
        self,
        *,
        task_id: UUID,
        query: str,
        constraints: dict[str, Any],
        existing_search_results: list[dict[str, Any]] | None = None,
    ) -> ResearchPlan:
        del task_id, existing_search_results
        normalized_query = query.strip()
        if not normalized_query:
            raise ResearchPlannerError(
                message="research planner received an empty query",
                reason="empty_query",
            )

        request = LLMRequest(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=USER_PROMPT_TEMPLATE.format(query=normalized_query),
            model=self.model,
            max_output_tokens=self.max_output_tokens,
            temperature=0.0,
            metadata={
                "query": normalized_query,
                "constraints": _safe_constraints(constraints),
                "purpose": "research_planner_v1",
            },
        )
        try:
            response = self.provider.generate(request)
        except LLMError as error:
            raise ResearchPlannerError(
                message="research planner LLM provider failed",
                reason="llm_provider_failed",
                details={"llm_error": error.to_payload()},
            ) from error

        return self.parse_plan(
            response.text,
            query=normalized_query,
            planner_mode="noop" if isinstance(self.provider, NoopLLMProvider) else "llm",
        )

    def parse_plan(
        self,
        text: str,
        *,
        query: str,
        planner_mode: str,
    ) -> ResearchPlan:
        payload = _parse_json_object(text)
        if payload is None:
            raise ResearchPlannerError(
                message="planner output was not valid JSON",
                reason="invalid_json",
            )
        return _plan_from_payload(
            payload,
            query=query,
            planner_mode=planner_mode,
            max_subquestions=self.max_subquestions,
            max_search_queries=self.max_search_queries,
        )


def create_research_planner_service(settings: Settings) -> ResearchPlannerService | None:
    if not settings.research_planner_enabled or not settings.llm_enabled:
        return None
    return ResearchPlannerService(
        provider=create_llm_provider(settings),
        model=settings.llm_model.strip() or settings.llm_provider.strip().lower() or "noop",
        max_output_tokens=settings.llm_max_output_tokens,
        max_subquestions=settings.research_planner_max_subquestions,
        max_search_queries=settings.research_planner_max_search_queries,
    )


def build_basic_research_plan(
    query: str,
    *,
    max_subquestions: int,
    max_search_queries: int,
    planner_mode: str,
) -> ResearchPlan:
    subject = _subject_from_query(query)
    subquestions = [
        f"What is {subject}?",
        f"How does {subject} work?",
        f"What privacy or design goals does {subject} have?",
        f"What features or integrations does {subject} support?",
    ][:max_subquestions]
    search_queries = [
        PlannedSearchQuery(
            query_text=f"{subject} official documentation what is {subject}",
            rationale="Find the official definition and overview.",
            expected_source_type="official_docs",
            priority=1,
            query_source="planner_query",
        ),
        PlannedSearchQuery(
            query_text=f"{subject} how it works metasearch engine upstream search engines",
            rationale="Find how the system operates and aggregates results.",
            expected_source_type="official_docs",
            priority=2,
            query_source="planner_query",
        ),
        PlannedSearchQuery(
            query_text=f"{subject} privacy not storing user information",
            rationale="Find privacy and data-handling behavior.",
            expected_source_type="official_docs",
            priority=3,
            query_source="planner_query",
        ),
        PlannedSearchQuery(
            query_text=f"{subject} features integrations limitations",
            rationale="Find feature, integration, and limitation details.",
            expected_source_type="reference",
            priority=4,
            query_source="planner_query",
        ),
    ][:max_search_queries]
    return ResearchPlan(
        intent="definition_how_it_works",
        normalized_question=query,
        subquestions=subquestions,
        search_queries=search_queries,
        source_preferences={
            "preferred_domains": [],
            "avoid_domains": [
                "reddit.com",
                "youtube.com",
                "facebook.com",
                "x.com",
                "twitter.com",
                "tiktok.com",
            ],
            "freshness_required": False,
        },
        answer_outline=[
            "Definition",
            "How it works",
            "Privacy model",
            "Features and integrations",
        ],
        risk_notes=["Prefer official documentation and stable reference sources."],
        planner_mode=planner_mode,
        warnings=[],
        answer_slots=[slot.to_payload() for slot in answer_slots_for_query(query)],
    )


def _parse_json_object(text: str) -> dict[str, Any] | None:
    stripped = _extract_json_object_text(text)
    if stripped is None:
        return None
    try:
        payload = json.loads(stripped)
    except (JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _extract_json_object_text(text: str) -> str | None:
    stripped = text.strip()
    if not stripped:
        return None

    fenced = _strip_markdown_json_fence(stripped)
    if fenced is not None:
        stripped = fenced

    start = stripped.find("{")
    if start < 0:
        return None

    in_string = False
    escaped = False
    depth = 0
    for index in range(start, len(stripped)):
        char = stripped[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue

        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : index + 1]
    return None


def _strip_markdown_json_fence(text: str) -> str | None:
    if not text.startswith("```"):
        return None
    lines = text.splitlines()
    if len(lines) < 2:
        return None
    if not lines[0].strip().startswith("```"):
        return None
    if lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return "\n".join(lines[1:]).strip()


def _plan_from_payload(
    payload: dict[str, Any],
    *,
    query: str,
    planner_mode: str,
    max_subquestions: int,
    max_search_queries: int,
) -> ResearchPlan:
    fallback = build_basic_research_plan(
        query,
        max_subquestions=max_subquestions,
        max_search_queries=max_search_queries,
        planner_mode=planner_mode,
    )
    intent = _string_value(payload.get("intent"), default="general")
    normalized_question = _string_value(payload.get("normalized_question"), default=query)
    subquestions = _string_list(payload.get("subquestions"))[:max_subquestions]
    if not subquestions:
        subquestions = fallback.subquestions
    search_queries = _planned_search_queries(payload.get("search_queries"))[:max_search_queries]
    if not search_queries:
        search_queries = fallback.search_queries
    source_preferences = payload.get("source_preferences")
    if not isinstance(source_preferences, dict):
        source_preferences = {}
    source_preferences = _source_preferences_with_defaults(
        source_preferences,
        fallback.source_preferences,
    )
    answer_outline = _string_list(payload.get("answer_outline"))
    if not answer_outline:
        answer_outline = fallback.answer_outline
    risk_notes = _string_list(payload.get("risk_notes"))
    if not risk_notes:
        risk_notes = fallback.risk_notes
    warnings = _string_list(payload.get("warnings"))
    if "planner_mode" not in payload:
        warnings.append("Planner output omitted planner_mode; using configured planner mode.")

    plan = ResearchPlan(
        intent=intent,
        normalized_question=normalized_question,
        subquestions=subquestions,
        search_queries=search_queries,
        source_preferences=source_preferences,
        answer_outline=answer_outline,
        risk_notes=risk_notes,
        planner_mode=planner_mode,
        warnings=warnings,
    )
    return _apply_research_plan_guardrails(
        plan,
        query=query,
        max_search_queries=max_search_queries,
    )


def _apply_research_plan_guardrails(
    plan: ResearchPlan,
    *,
    query: str,
    max_search_queries: int,
) -> ResearchPlan:
    intent_classification = _classify_research_intent(query=query, intent=plan.intent)
    overview_query = intent_classification == "overview_definition_intent"
    extracted_entity = _extract_entity_from_overview_query(query) if overview_query else None
    searxng_query = _is_searxng_query(query)
    source_preferences = dict(plan.source_preferences)
    warnings = list(plan.warnings)
    guardrail_warnings: list[str] = []
    raw_planner_queries = [item.to_payload() for item in plan.search_queries]
    overridden_avoid_domains: list[str] = []
    dropped_or_downweighted: list[dict[str, Any]] = []

    if overview_query:
        avoid_domains = _string_list(source_preferences.get("avoid_domains"))
        kept_avoid_domains: list[str] = []
        for domain in avoid_domains:
            if _is_wikipedia_domain(domain):
                overridden_avoid_domains.append(_canonical_wikipedia_domain_warning(domain))
                continue
            kept_avoid_domains.append(domain)
        if overridden_avoid_domains:
            source_preferences["avoid_domains"] = kept_avoid_domains
            source_preferences["overridden_avoid_domains"] = list(
                dict.fromkeys(overridden_avoid_domains)
            )
            override_warning = "planner_avoid_domains_overridden_for_overview_reference"
            guardrail_warnings.append(override_warning)
            warnings.append(override_warning)
            for domain in source_preferences["overridden_avoid_domains"]:
                warnings.append(
                    f"planner_avoid_domain_overridden: {domain}; "
                    "reason: reference source allowed for definition query"
                )

    if overview_query and searxng_query:
        preferred_domains = _string_list(source_preferences.get("preferred_domains"))
        source_preferences["preferred_domains"] = _prepend_unique_strings(
            [
                "docs.searxng.org",
                "searxng.org",
                "en.wikipedia.org",
            ],
            preferred_domains,
        )

    search_queries = plan.search_queries
    if overview_query and extracted_entity:
        guardrail_queries = [
            PlannedSearchQuery(
                query_text=query,
                rationale="Preserve the user's original overview question.",
                expected_source_type="general_web",
                priority=1,
                query_source="original_user_query",
            ),
            PlannedSearchQuery(
                query_text=f"{extracted_entity} official documentation",
                rationale="Prioritize official documentation for the overview definition.",
                expected_source_type="official_docs",
                priority=2,
                query_source="guardrail_query",
            ),
            PlannedSearchQuery(
                query_text=f"{extracted_entity} about how does it work",
                rationale="Prioritize official about or mechanism-oriented explanations.",
                expected_source_type="official_about",
                priority=3,
                query_source="guardrail_query",
            ),
            PlannedSearchQuery(
                query_text=f"{extracted_entity} Wikipedia",
                rationale="Prioritize a stable reference source for the definition.",
                expected_source_type="reference",
                priority=4,
                query_source="guardrail_query",
            ),
        ]
        if _looks_like_software_project_query(query=query, entity=extracted_entity):
            guardrail_queries.append(
                PlannedSearchQuery(
                    query_text=f"{extracted_entity} GitHub README",
                    rationale="Find an upstream repository README for project-level context.",
                    expected_source_type="github_readme_or_repo",
                    priority=5,
                    query_source="guardrail_query",
                )
            )

        search_queries, dropped_or_downweighted = _merge_and_rank_planned_queries(
            guardrail_queries,
            plan.search_queries,
            max_search_queries=max_search_queries,
            demote_architecture_or_setup=not _query_explicitly_asks_admin_or_setup(query),
        )

    final_search_queries = [item.to_payload() for item in search_queries]
    return ResearchPlan(
        intent=plan.intent,
        normalized_question=plan.normalized_question,
        subquestions=plan.subquestions,
        search_queries=search_queries,
        source_preferences=source_preferences,
        answer_outline=plan.answer_outline,
        risk_notes=plan.risk_notes,
        planner_mode=plan.planner_mode,
        warnings=list(dict.fromkeys(warnings)),
        answer_slots=[slot.to_payload() for slot in answer_slots_for_query(query)],
        raw_planner_queries=raw_planner_queries,
        final_search_queries=final_search_queries,
        dropped_or_downweighted_planner_queries=dropped_or_downweighted,
        planner_guardrail_warnings=list(dict.fromkeys(guardrail_warnings)),
        intent_classification=intent_classification,
        extracted_entity=extracted_entity,
    )


def _source_preferences_with_defaults(
    value: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    preferred_domains = _string_list(value.get("preferred_domains"))
    avoid_domains = _string_list(value.get("avoid_domains"))
    freshness_required = value.get("freshness_required")
    return {
        "preferred_domains": preferred_domains or _string_list(defaults.get("preferred_domains")),
        "avoid_domains": avoid_domains or _string_list(defaults.get("avoid_domains")),
        "freshness_required": (
            freshness_required
            if isinstance(freshness_required, bool)
            else bool(defaults.get("freshness_required", False))
        ),
    }


def _merge_and_rank_planned_queries(
    guardrail_queries: list[PlannedSearchQuery],
    planner_queries: list[PlannedSearchQuery],
    *,
    max_search_queries: int,
    demote_architecture_or_setup: bool,
) -> tuple[list[PlannedSearchQuery], list[dict[str, Any]]]:
    merged: list[PlannedSearchQuery] = []
    seen: set[str] = set()
    dropped_or_downweighted: list[dict[str, Any]] = []

    def add(query: PlannedSearchQuery) -> None:
        normalized = query.query_text.strip()
        dedupe_key = normalized.lower()
        if not normalized or dedupe_key in seen:
            return
        merged.append(
            PlannedSearchQuery(
                query_text=normalized,
                rationale=query.rationale,
                expected_source_type=query.expected_source_type,
                priority=query.priority,
                query_source=query.query_source,
            )
        )
        seen.add(dedupe_key)

    for query in guardrail_queries:
        add(query)

    planner_with_penalty = [
        (
            query,
            _planner_query_guardrail_penalty(query) if demote_architecture_or_setup else 0,
        )
        for query in planner_queries
    ]
    planner_sorted = sorted(
        planner_with_penalty,
        key=lambda item: (
            item[1],
            item[0].priority,
            item[0].query_text.lower(),
        ),
    )
    for query, penalty in planner_sorted:
        if penalty > 0:
            dropped_or_downweighted.append(
                {
                    **query.to_payload(),
                    "action": "downweighted",
                    "downrank_reason": _planner_query_downrank_reason(query),
                    "guardrail_penalty": penalty,
                }
            )
        add(query)

    effective_max_queries = max(max_search_queries, len(guardrail_queries))
    capped = merged[:effective_max_queries]
    dropped_texts = {query.query_text.lower() for query in merged[effective_max_queries:]}
    for query in merged[effective_max_queries:]:
        if query.query_source == "planner_query":
            dropped_or_downweighted.append(
                {
                    **query.to_payload(),
                    "action": "dropped",
                    "downrank_reason": "query_cap_after_guardrail_queries",
                }
            )
    final_queries = [
        PlannedSearchQuery(
            query_text=query.query_text,
            rationale=query.rationale,
            expected_source_type=query.expected_source_type,
            priority=index,
            query_source=query.query_source,
        )
        for index, query in enumerate(capped, start=1)
    ]
    deduped_diagnostics: list[dict[str, Any]] = []
    seen_diagnostics: set[tuple[str, str]] = set()
    for item in dropped_or_downweighted:
        key = (str(item.get("query_text", "")).lower(), str(item.get("action", "")))
        if key in seen_diagnostics or item.get("query_text", "").lower() in dropped_texts:
            continue
        deduped_diagnostics.append(item)
        seen_diagnostics.add(key)
    for item in dropped_or_downweighted:
        key = (str(item.get("query_text", "")).lower(), str(item.get("action", "")))
        if key in seen_diagnostics:
            continue
        if item.get("action") == "dropped":
            deduped_diagnostics.append(item)
            seen_diagnostics.add(key)
    return final_queries, deduped_diagnostics


def _planner_query_guardrail_penalty(query: PlannedSearchQuery) -> int:
    lower = query.query_text.lower()
    if any(term in lower for term in ("architecture", "admin", "api", "developer", "dev/")):
        return 20
    if any(term in lower for term in ("install", "installation", "setup", "docker")):
        return 20
    if any(term in lower for term in ("compare", "comparison", "alternative", "vs ")):
        return 8
    return 0


def _planner_query_downrank_reason(query: PlannedSearchQuery) -> str:
    lower = query.query_text.lower()
    if any(term in lower for term in ("architecture", "admin")):
        return "architecture_or_admin_query_downweighted_for_overview"
    if any(term in lower for term in ("api", "developer", "dev/")):
        return "api_or_developer_query_downweighted_for_overview"
    if any(term in lower for term in ("install", "installation", "setup", "docker")):
        return "installation_or_setup_query_downweighted_for_overview"
    if any(term in lower for term in ("compare", "comparison", "alternative", "vs ")):
        return "comparison_query_kept_but_lower_priority_for_overview"
    return "planner_query_lower_priority_after_guardrail"


def _classify_research_intent(*, query: str, intent: str) -> str:
    if _is_definition_or_overview_query(query=query, intent=intent):
        return "overview_definition_intent"
    return "general_research_intent"


def _is_definition_or_overview_query(*, query: str, intent: str) -> bool:
    lower_query = query.lower()
    lower_intent = intent.lower()
    if any(term in lower_intent for term in ("definition", "overview", "what", "how_it_works")):
        return True
    return (
        "what is" in lower_query
        or "what are" in lower_query
        or lower_query.startswith("explain ")
        or "overview" in lower_query
        or " features and mechanism" in lower_query
        or "how does" in lower_query
        or "how do" in lower_query
    )


def _is_searxng_query(query: str) -> bool:
    return "searxng" in query.lower()


def _is_searxng_what_how_query(query: str) -> bool:
    lower = query.lower()
    return "searxng" in lower and "what is" in lower and "how" in lower and "work" in lower


def _query_explicitly_asks_admin_or_setup(query: str) -> bool:
    lower = query.lower()
    return any(
        term in lower
        for term in (
            "admin",
            "architecture",
            "api",
            "configure",
            "developer",
            "deploy",
            "deployment",
            "docker",
            "install",
            "installation",
            "setup",
        )
    )


def _is_wikipedia_domain(domain: str) -> bool:
    normalized = domain.strip().lower().lstrip(".").removeprefix("www.")
    return normalized == "wikipedia.org" or normalized.endswith(".wikipedia.org")


def _canonical_wikipedia_domain_warning(domain: str) -> str:
    normalized = domain.strip().lower().lstrip(".").removeprefix("www.")
    return "wikipedia.org" if normalized == "wikipedia.org" else normalized


def _prepend_unique_strings(prefix: list[str], values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in [*prefix, *values]:
        normalized = value.strip()
        key = normalized.lower()
        if not normalized or key in seen:
            continue
        result.append(normalized)
        seen.add(key)
    return result


def _extract_entity_from_overview_query(query: str) -> str | None:
    normalized = query.strip().strip("\"'").rstrip("?.!")
    if not normalized:
        return None

    patterns = (
        r"(?i)^what\s+(?:is|are)\s+(.+?)(?:\s+and\s+how\b|\s+how\b|$)",
        r"(?i)^explain\s+(.+?)(?:\s+and\s+how\b|\s+how\b|$)",
        r"(?i)^(.+?)\s+overview$",
        r"(?i)^(.+?)\s+features\s+and\s+mechanism$",
    )
    for pattern in patterns:
        match = re.match(pattern, normalized)
        if match is None:
            continue
        candidate = _clean_entity(match.group(1))
        if candidate:
            return candidate

    return _clean_entity(_subject_from_query(normalized))


def _clean_entity(value: str) -> str | None:
    cleaned = re.sub(r"(?i)\b(?:please|briefly|explain|overview)\b", " ", value)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;")
    if not cleaned:
        return None
    words = cleaned.split()
    if len(words) > 6:
        return " ".join(words[:6]).strip()
    return cleaned


def _looks_like_software_project_query(*, query: str, entity: str) -> bool:
    lower = f"{query} {entity}".lower()
    if any(
        term in lower
        for term in (
            "app",
            "engine",
            "github",
            "open-source",
            "opensource",
            "project",
            "self-host",
            "software",
            "tool",
        )
    ):
        return True
    return any(char.isupper() or char.isdigit() for char in entity)


def _planned_search_queries(value: Any) -> list[PlannedSearchQuery]:
    if not isinstance(value, list):
        return []
    planned: list[PlannedSearchQuery] = []
    seen_query_texts: set[str] = set()
    for index, item in enumerate(value, start=1):
        if isinstance(item, str):
            query_text = item.strip()
            rationale = ""
            expected_source_type = "general_web"
            priority = index
        elif isinstance(item, dict):
            query_text = _string_value(item.get("query_text"), default="")
            rationale = _string_value(item.get("rationale"), default="")
            expected_source_type = _string_value(
                item.get("expected_source_type"),
                default="general_web",
            )
            query_source = _string_value(item.get("query_source"), default="planner_query")
            priority = item.get("priority")
            if not isinstance(priority, int):
                priority = index
        else:
            continue
        if isinstance(item, str):
            query_source = "planner_query"
        if not query_text or query_text in seen_query_texts:
            continue
        planned.append(
            PlannedSearchQuery(
                query_text=query_text,
                rationale=rationale,
                expected_source_type=expected_source_type,
                priority=priority,
                query_source=query_source,
            )
        )
        seen_query_texts.add(query_text)
    return sorted(planned, key=lambda query: query.priority)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    strings: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        strings.append(normalized)
        seen.add(normalized)
    return strings


def _string_value(value: Any, *, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _safe_constraints(constraints: dict[str, Any]) -> dict[str, Any]:
    safe_constraints: dict[str, Any] = {}
    for key, value in constraints.items():
        normalized_key = str(key).lower()
        if "key" in normalized_key or "secret" in normalized_key or "password" in normalized_key:
            safe_constraints[key] = "[redacted]"
        else:
            safe_constraints[key] = value
    return safe_constraints


def _subject_from_query(query: str) -> str:
    normalized = query.strip().rstrip("?")
    lower = normalized.lower()
    if lower.startswith("what is "):
        remainder = normalized[8:].strip()
        lower_remainder = remainder.lower()
        marker = " and how"
        if marker in lower_remainder:
            return remainder[: lower_remainder.index(marker)].strip() or normalized
        return remainder or normalized
    return normalized
