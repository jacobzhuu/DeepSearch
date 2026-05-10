from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from json import JSONDecodeError
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    ValidationError,
    field_validator,
)

from services.orchestrator.app.llm import (
    LLMError,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    create_llm_provider,
)
from services.orchestrator.app.llm.providers import NoopLLMProvider
from services.orchestrator.app.planning.types import PlannedSearchQuery, ResearchPlan
from services.orchestrator.app.research_quality import answer_slots_for_query
from services.orchestrator.app.settings import Settings

SYSTEM_PROMPT = """You plan evidence-first web research.
Return one JSON object only. Do not use markdown fences, explanations, claims, or final answers."""

PLANNER_PROMPT_VERSION = "research_planner_v1"
PLANNER_OUTPUT_SCHEMA_VERSION = "research_planner_output_v1"
MAX_RAW_OUTPUT_PREVIEW_CHARS = 500
DISABLED_PLANNER_WARNING = "No LLM planner is active; deterministic planner used."
LLM_PLANNER_SUCCESS_WARNING = "LLM planner generated this research plan."
LLM_PLANNER_FALLBACK_WARNING = (
    "LLM planner failed validation/provider call; deterministic fallback was used."
)
ALLOWED_EXPECTED_SOURCE_TYPES = (
    "general_web",
    "official_docs",
    "official_about",
    "official_installation_admin",
    "official_or_reference",
    "official_repository",
    "github_readme_or_repo",
    "reference",
)
LANGGRAPH_GUARDRAIL_PREFERRED_DOMAINS = (
    "docs.langchain.com",
    "reference.langchain.com",
    "www.langchain.com",
    "langchain.com",
    "github.com/langchain-ai/langgraph",
)
LANGGRAPH_WEAK_SECONDARY_DOMAINS = (
    "langchain-ai.github.io",
    "blog.langchain.dev",
)
LANGGRAPH_BROAD_GITHUB_PREFERENCE = "github.com/langchain-ai"
LANGGRAPH_UPSTREAM_GITHUB_PREFERENCE = "github.com/langchain-ai/langgraph"
ExpectedSourceType = Literal[
    "general_web",
    "official_docs",
    "official_about",
    "official_installation_admin",
    "official_or_reference",
    "official_repository",
    "github_readme_or_repo",
    "reference",
]

USER_PROMPT_TEMPLATE = """Create a bounded research plan for this query.

Current date: {current_date}

Query:
{query}

Return raw JSON only. Do not wrap it in markdown fences. Do not include explanatory text.

Return one JSON object with exactly these top-level keys:
intent, normalized_question, subquestions, search_queries, source_preferences,
answer_outline, risk_notes, planner_mode, warnings.

Use no more than {max_subquestions} subquestions and no more than
{max_search_queries} search_queries.
Use planner_mode = "llm".

Each search_queries item must include query_text, rationale, expected_source_type, priority.
Use only these expected_source_type values:
{allowed_expected_source_types}.

source_preferences must include preferred_domains, avoid_domains, and freshness_required.
preferred_domains and avoid_domains must be arrays of strings.
freshness_required must be a JSON boolean true or false.
answer_outline, risk_notes, and warnings must be arrays of strings.
priority must be a JSON integer.
Prefer official and reference sources when possible.
For time-sensitive queries such as "last 30 days", "recent", "latest", or "近30天",
keep search queries anchored to the current date and do not use stale years.

Schema-conforming compact example:
{example_json}"""


class _LLMPlannerSearchQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_text: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    expected_source_type: ExpectedSourceType
    priority: StrictInt

    @field_validator("query_text", "rationale", "expected_source_type", mode="before")
    @classmethod
    def _strip_required_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class _LLMPlannerSourcePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preferred_domains: list[str]
    avoid_domains: list[str]
    freshness_required: StrictBool


class _LLMPlannerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = Field(min_length=1)
    normalized_question: str = Field(min_length=1)
    subquestions: list[str] = Field(min_length=1)
    search_queries: list[_LLMPlannerSearchQuery] = Field(min_length=1)
    source_preferences: _LLMPlannerSourcePreferences
    answer_outline: list[str]
    risk_notes: list[str]
    planner_mode: str = Field(min_length=1)
    warnings: list[str]

    @field_validator("intent", "normalized_question", "planner_mode", mode="before")
    @classmethod
    def _strip_required_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("subquestions", "answer_outline", "risk_notes", "warnings")
    @classmethod
    def _strip_string_list(cls, values: list[str]) -> list[str]:
        stripped: list[str] = []
        for value in values:
            if not isinstance(value, str):
                raise ValueError("all items must be strings")
            normalized = value.strip()
            if normalized:
                stripped.append(normalized)
        return stripped


@dataclass(frozen=True)
class PlannerRunResult:
    plan: ResearchPlan
    plan_source: str
    planner_status: str
    warnings: list[str]
    failure: dict[str, Any] | None = None


@dataclass(frozen=True)
class _PlannerJsonParseResult:
    payload: dict[str, Any] | None
    diagnostics: dict[str, Any]


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
            user_prompt=USER_PROMPT_TEMPLATE.format(
                current_date=_current_date_iso(),
                query=normalized_query,
                max_subquestions=self.max_subquestions,
                max_search_queries=self.max_search_queries,
                allowed_expected_source_types=", ".join(ALLOWED_EXPECTED_SOURCE_TYPES),
                example_json=_compact_planner_example_json(),
            ),
            model=self.model,
            max_output_tokens=self.max_output_tokens,
            temperature=0.0,
            metadata={
                "query": normalized_query,
                "constraints": _safe_constraints(constraints),
                "purpose": PLANNER_PROMPT_VERSION,
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
            response=response,
        )

    def parse_plan(
        self,
        text: str,
        *,
        query: str,
        planner_mode: str,
        response: LLMResponse | None = None,
    ) -> ResearchPlan:
        parse_result = _parse_json_object_with_diagnostics(text)
        if parse_result.payload is None:
            raise ResearchPlannerError(
                message="planner output was not valid JSON",
                reason="invalid_json",
                details=parse_result.diagnostics,
            )
        strict_payload = _validate_llm_plan_payload(
            parse_result.payload,
            diagnostics=parse_result.diagnostics,
        )
        plan = _plan_from_payload(
            strict_payload,
            query=query,
            planner_mode=planner_mode,
            max_subquestions=self.max_subquestions,
            max_search_queries=self.max_search_queries,
        )
        return _plan_with_updates(
            plan,
            diagnostics=_planner_success_diagnostics(
                response=response,
                raw_text=text,
                parsed_payload=strict_payload,
                parse_diagnostics=parse_result.diagnostics,
            ),
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
    if _is_deployment_query(query):
        subquestions = [
            f"What is the deployment target for {subject}?",
            f"What Docker or container steps are needed to deploy {subject}?",
            f"What configuration, secrets, storage, and network settings does {subject} need?",
            f"What operational limitations or caveats apply to a {subject} deployment?",
        ][:max_subquestions]
        search_queries = [
            PlannedSearchQuery(
                query_text=f"{subject} Docker deployment official documentation",
                rationale="Find official Docker deployment guidance.",
                expected_source_type="official_installation_admin",
                priority=1,
                query_source="planner_query",
            ),
            PlannedSearchQuery(
                query_text=f"{subject} docker compose installation configuration settings",
                rationale="Find required container configuration and setup steps.",
                expected_source_type="official_docs",
                priority=2,
                query_source="planner_query",
            ),
            PlannedSearchQuery(
                query_text=f"{subject} deployment environment variables secrets reverse proxy",
                rationale="Find operational configuration and exposure requirements.",
                expected_source_type="official_docs",
                priority=3,
                query_source="planner_query",
            ),
            PlannedSearchQuery(
                query_text=f"{subject} Docker deployment limitations troubleshooting",
                rationale="Find caveats and common deployment failure modes.",
                expected_source_type="reference",
                priority=4,
                query_source="planner_query",
            ),
        ][:max_search_queries]
        return ResearchPlan(
            intent="deployment",
            normalized_question=query,
            subquestions=subquestions,
            search_queries=search_queries,
            source_preferences=_default_source_preferences(),
            answer_outline=[
                "Deployment target",
                "Deployment steps",
                "Configuration",
                "Operational limitations",
            ],
            risk_notes=[
                "Prefer official installation and operations documentation.",
                (
                    "Treat secrets, public exposure, storage, and reverse-proxy settings as "
                    "operational risk areas."
                ),
            ],
            planner_mode=planner_mode,
            warnings=[],
            answer_slots=[slot.to_payload() for slot in answer_slots_for_query(query)],
        )

    subquestions = [
        f"What is {subject}?",
        f"How does {subject} work?",
        f"What privacy or design goals does {subject} have?",
        f"What features or integrations does {subject} support?",
    ][:max_subquestions]
    if subject.lower() == "searxng":
        mechanism_query = f"{subject} how it works metasearch engine upstream search engines"
        mechanism_rationale = "Find how the system operates and aggregates results."
        trust_query = f"{subject} privacy not storing user information"
        trust_rationale = "Find privacy and data-handling behavior."
    else:
        mechanism_query = f"{subject} how it works state graph nodes edges workflow"
        mechanism_rationale = "Find how the system operates and routes work."
        trust_query = f"{subject} privacy trust security human-in-the-loop data storage"
        trust_rationale = "Find trust, privacy, security, and data-handling behavior."

    search_queries = [
        PlannedSearchQuery(
            query_text=f"{subject} official documentation what is {subject}",
            rationale="Find the official definition and overview.",
            expected_source_type="official_docs",
            priority=1,
            query_source="planner_query",
        ),
        PlannedSearchQuery(
            query_text=mechanism_query,
            rationale=mechanism_rationale,
            expected_source_type="official_docs",
            priority=2,
            query_source="planner_query",
        ),
        PlannedSearchQuery(
            query_text=trust_query,
            rationale=trust_rationale,
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
        source_preferences=_default_source_preferences(),
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


def build_default_research_plan(
    query: str,
    *,
    max_subquestions: int,
    max_search_queries: int,
    planner_mode: str = "deterministic",
) -> ResearchPlan:
    plan = build_basic_research_plan(
        query,
        max_subquestions=max_subquestions,
        max_search_queries=max_search_queries,
        planner_mode=planner_mode,
    )
    return _apply_research_plan_guardrails(
        plan,
        query=query,
        max_search_queries=max_search_queries,
    )


def build_research_plan_from_payload(
    payload: dict[str, Any],
    *,
    query: str,
    planner_mode: str,
    max_subquestions: int,
    max_search_queries: int,
) -> ResearchPlan:
    return _plan_from_payload(
        payload,
        query=query,
        planner_mode=planner_mode,
        max_subquestions=max_subquestions,
        max_search_queries=max_search_queries,
    )


def build_optional_research_plan(
    *,
    planner_service: ResearchPlannerService | None,
    task_id: UUID,
    query: str,
    constraints: dict[str, Any],
    max_subquestions: int,
    max_search_queries: int,
    disabled_plan_source: str = "deterministic_fallback",
    llm_plan_source: str = "llm_planner",
    failure_plan_source: str = "deterministic_fallback_after_llm_failure",
) -> PlannerRunResult:
    if planner_service is None:
        plan = build_default_research_plan(
            query,
            max_subquestions=max_subquestions,
            max_search_queries=max_search_queries,
            planner_mode="deterministic",
        )
        plan = _plan_with_updates(plan, warnings=[DISABLED_PLANNER_WARNING])
        return PlannerRunResult(
            plan=plan,
            plan_source=disabled_plan_source,
            planner_status="created",
            warnings=list(plan.warnings),
        )

    try:
        plan = planner_service.plan(task_id=task_id, query=query, constraints=constraints)
    except ResearchPlannerError as error:
        failure = error.to_payload()
        reason_warning = f"research planner failed; using deterministic fallback ({error.reason})."
        fallback_plan = build_default_research_plan(
            query,
            max_subquestions=max_subquestions,
            max_search_queries=max_search_queries,
            planner_mode="deterministic",
        )
        fallback_plan = _plan_with_updates(
            fallback_plan,
            warnings=[LLM_PLANNER_FALLBACK_WARNING, reason_warning],
            diagnostics={
                "planner_fallback": True,
                "fallback_reason": error.reason,
                "planner_failure": failure,
                "planner_prompt_version": PLANNER_PROMPT_VERSION,
                "planner_output_schema_version": PLANNER_OUTPUT_SCHEMA_VERSION,
                **_failure_diagnostics(error),
            },
        )
        return PlannerRunResult(
            plan=fallback_plan,
            plan_source=failure_plan_source,
            planner_status="fallback",
            warnings=list(fallback_plan.warnings),
            failure=failure,
        )

    if plan.planner_mode == "llm":
        plan = _plan_with_updates(plan, warnings=[LLM_PLANNER_SUCCESS_WARNING])
    return PlannerRunResult(
        plan=plan,
        plan_source=llm_plan_source if plan.planner_mode == "llm" else "noop_planner",
        planner_status="success" if plan.planner_mode == "llm" else "created",
        warnings=list(plan.warnings),
    )


def research_plan_from_serialized_payload(payload: dict[str, Any]) -> ResearchPlan | None:
    search_queries = _planned_search_queries(payload.get("search_queries"))
    if not search_queries:
        return None
    intent = _string_value(payload.get("intent"), default="general")
    normalized_question = _string_value(
        payload.get("normalized_question"),
        default="Research question",
    )
    planner_mode = _string_value(payload.get("planner_mode"), default="event")
    source_preferences = payload.get("source_preferences")
    if not isinstance(source_preferences, dict):
        source_preferences = {}
    planner_diagnostics = payload.get("planner_diagnostics")
    return ResearchPlan(
        intent=intent,
        normalized_question=normalized_question,
        subquestions=_string_list(payload.get("subquestions")),
        search_queries=search_queries,
        source_preferences=dict(source_preferences),
        answer_outline=_string_list(payload.get("answer_outline")),
        risk_notes=_string_list(payload.get("risk_notes")),
        planner_mode=planner_mode,
        warnings=_string_list(payload.get("warnings")),
        answer_slots=_object_list(payload.get("answer_slots")),
        raw_planner_queries=_object_list(payload.get("raw_planner_queries")),
        final_search_queries=_object_list(payload.get("final_search_queries")),
        dropped_or_downweighted_planner_queries=_object_list(
            payload.get("dropped_or_downweighted_planner_queries")
        ),
        planner_guardrail_warnings=_string_list(payload.get("planner_guardrail_warnings")),
        intent_classification=_optional_string(payload.get("intent_classification")),
        extracted_entity=_optional_string(payload.get("extracted_entity")),
        planner_diagnostics=(
            dict(planner_diagnostics) if isinstance(planner_diagnostics, dict) else {}
        ),
    )


def _validate_llm_plan_payload(
    payload: dict[str, Any],
    *,
    diagnostics: dict[str, Any],
) -> dict[str, Any]:
    try:
        validated = _LLMPlannerPayload.model_validate(payload)
    except ValidationError as error:
        validation_errors = _simplified_validation_errors(error)
        raise ResearchPlannerError(
            message="planner output did not match the required schema",
            reason="invalid_schema",
            details={
                **_schema_validation_diagnostics(diagnostics, schema_validated=False),
                "validation_errors": validation_errors,
            },
        ) from error

    normalized = validated.model_dump()
    if not _string_list(normalized.get("subquestions")):
        raise ResearchPlannerError(
            message="planner output did not include usable subquestions",
            reason="invalid_schema",
            details={
                **_schema_validation_diagnostics(diagnostics, schema_validated=False),
                "validation_errors": [
                    {
                        "loc": ["subquestions"],
                        "path": "subquestions",
                        "msg": "empty list",
                        "type": "value_error",
                        "category": "missing_field",
                    }
                ],
            },
        )
    if not _planned_search_queries(normalized.get("search_queries")):
        raise ResearchPlannerError(
            message="planner output did not include usable search queries",
            reason="invalid_schema",
            details={
                **_schema_validation_diagnostics(diagnostics, schema_validated=False),
                "validation_errors": [
                    {
                        "loc": ["search_queries"],
                        "path": "search_queries",
                        "msg": "empty list",
                        "type": "value_error",
                        "category": "missing_field",
                    }
                ],
            },
        )
    return normalized


def _simplified_validation_errors(error: ValidationError) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    for item in error.errors():
        loc = list(item.get("loc", ()))
        error_type = str(item.get("type", ""))
        simplified: dict[str, Any] = {
            "loc": loc,
            "path": ".".join(str(part) for part in loc),
            "msg": str(item.get("msg", "")),
            "type": error_type,
            "category": _validation_error_category(loc=loc, error_type=error_type),
        }
        if simplified["category"] == "invalid_enum_value":
            simplified["allowed_values"] = list(ALLOWED_EXPECTED_SOURCE_TYPES)
        errors.append(simplified)
    return errors


def _validation_error_category(*, loc: list[Any], error_type: str) -> str:
    if error_type == "missing":
        return "missing_field"
    if error_type == "extra_forbidden":
        return "extra_field"
    if error_type == "literal_error" or (
        loc and loc[-1] == "expected_source_type" and error_type.startswith("value_error")
    ):
        return "invalid_enum_value"
    if error_type.endswith("_type") or error_type.endswith("_parsing"):
        return "wrong_type"
    return "validation_error"


def _planner_success_diagnostics(
    *,
    response: LLMResponse | None,
    raw_text: str,
    parsed_payload: dict[str, Any],
    parse_diagnostics: dict[str, Any],
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        **_schema_validation_diagnostics(parse_diagnostics, schema_validated=True),
        "planner_fallback": False,
        "planner_prompt_version": PLANNER_PROMPT_VERSION,
        "planner_output_schema_version": PLANNER_OUTPUT_SCHEMA_VERSION,
        "raw_output_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        "parsed_output": parsed_payload,
    }
    if response is not None:
        diagnostics.update(
            {
                "provider": response.provider,
                "model": response.model,
                "usage": dict(response.usage or {}),
                "raw_response_id": response.raw_response_id,
                "finish_reason": response.finish_reason,
            }
        )
    return diagnostics


def _plan_with_updates(
    plan: ResearchPlan,
    *,
    warnings: list[str] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> ResearchPlan:
    combined_warnings = list(plan.warnings)
    if warnings:
        combined_warnings.extend(warnings)
    planner_diagnostics = dict(plan.planner_diagnostics)
    if diagnostics:
        planner_diagnostics.update(diagnostics)
    return ResearchPlan(
        intent=plan.intent,
        normalized_question=plan.normalized_question,
        subquestions=list(plan.subquestions),
        search_queries=list(plan.search_queries),
        source_preferences=dict(plan.source_preferences),
        answer_outline=list(plan.answer_outline),
        risk_notes=list(plan.risk_notes),
        planner_mode=plan.planner_mode,
        warnings=list(dict.fromkeys(combined_warnings)),
        answer_slots=list(plan.answer_slots),
        raw_planner_queries=list(plan.raw_planner_queries),
        final_search_queries=list(plan.final_search_queries),
        dropped_or_downweighted_planner_queries=list(plan.dropped_or_downweighted_planner_queries),
        planner_guardrail_warnings=list(plan.planner_guardrail_warnings),
        intent_classification=plan.intent_classification,
        extracted_entity=plan.extracted_entity,
        planner_diagnostics=planner_diagnostics,
    )


def _parse_json_object_with_diagnostics(text: str) -> _PlannerJsonParseResult:
    extracted = _extract_json_object_text(text)
    diagnostics = _raw_output_diagnostics(
        raw_text=text,
        json_extracted=extracted is not None,
        json_extraction_method=extracted.method if extracted is not None else None,
        json_extraction_error=None if extracted is not None else _json_extraction_error(text),
        schema_validated=False,
    )
    if extracted is None:
        return _PlannerJsonParseResult(payload=None, diagnostics=diagnostics)
    try:
        payload = json.loads(extracted.json_text)
    except (JSONDecodeError, ValueError):
        diagnostics["json_extracted"] = False
        diagnostics["parse_stages"]["json_extracted"] = False
        diagnostics["json_extraction_error"] = "invalid_json"
        return _PlannerJsonParseResult(payload=None, diagnostics=diagnostics)
    if not isinstance(payload, dict):
        diagnostics["json_extracted"] = False
        diagnostics["parse_stages"]["json_extracted"] = False
        diagnostics["json_extraction_error"] = "json_root_not_object"
        return _PlannerJsonParseResult(payload=None, diagnostics=diagnostics)
    return _PlannerJsonParseResult(payload=payload, diagnostics=diagnostics)


@dataclass(frozen=True)
class _ExtractedJsonText:
    json_text: str
    method: str


def _extract_json_object_text(text: str) -> _ExtractedJsonText | None:
    stripped = text.strip()
    if not stripped:
        return None

    fenced = _extract_single_markdown_json_fence(stripped)
    if fenced is not None:
        return _ExtractedJsonText(json_text=fenced, method="single_fenced_json_object")

    if not stripped.startswith("{"):
        return None
    try:
        payload, end_index = json.JSONDecoder().raw_decode(stripped)
    except (JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if stripped[end_index:].strip():
        return None
    return _ExtractedJsonText(json_text=stripped, method="direct_json_object")


def _extract_single_markdown_json_fence(text: str) -> str | None:
    matches = list(
        re.finditer(
            r"```(?:json|JSON)?[^\S\r\n]*\r?\n(?P<body>.*?)\r?\n?```",
            text,
            flags=re.DOTALL,
        )
    )
    if len(matches) != 1:
        return None
    body = matches[0].group("body").strip()
    if not body:
        return None
    try:
        payload, end_index = json.JSONDecoder().raw_decode(body)
    except (JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if body[end_index:].strip():
        return None
    return body


def _json_extraction_error(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return "empty_output"
    if stripped.startswith("{") or stripped.startswith("```"):
        return "invalid_json"
    if "{" in stripped and "}" in stripped:
        return "prose_around_unfenced_json"
    return "no_standalone_json_object"


def _schema_validation_diagnostics(
    diagnostics: dict[str, Any],
    *,
    schema_validated: bool,
) -> dict[str, Any]:
    updated = dict(diagnostics)
    parse_stages = dict(updated.get("parse_stages") or {})
    parse_stages["schema_validated"] = schema_validated
    updated["parse_stages"] = parse_stages
    updated["schema_validated"] = schema_validated
    updated["parse_stage"] = "schema_validated" if schema_validated else "json_extracted"
    if schema_validated:
        updated.pop("json_extraction_error", None)
    return updated


def _raw_output_diagnostics(
    *,
    raw_text: str,
    json_extracted: bool,
    json_extraction_method: str | None,
    json_extraction_error: str | None,
    schema_validated: bool,
) -> dict[str, Any]:
    raw_preview, preview_truncated = _sanitized_preview(
        raw_text,
        max_chars=MAX_RAW_OUTPUT_PREVIEW_CHARS,
    )
    diagnostics: dict[str, Any] = {
        "parse_stage": "json_extracted" if json_extracted else "raw_text",
        "parse_stages": {
            "raw_text": True,
            "json_extracted": json_extracted,
            "schema_validated": schema_validated,
        },
        "raw_text": True,
        "json_extracted": json_extracted,
        "schema_validated": schema_validated,
        "json_extraction_method": json_extraction_method,
        "raw_output_preview": raw_preview,
        "raw_output_preview_truncated": preview_truncated,
        "raw_output_preview_max_chars": MAX_RAW_OUTPUT_PREVIEW_CHARS,
        "raw_output_sha256": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
    }
    if json_extraction_error is not None:
        diagnostics["json_extraction_error"] = json_extraction_error
    return diagnostics


def _failure_diagnostics(error: ResearchPlannerError) -> dict[str, Any]:
    details = dict(error.details or {})
    keys = {
        "parse_stage",
        "parse_stages",
        "raw_text",
        "json_extracted",
        "schema_validated",
        "json_extraction_method",
        "json_extraction_error",
        "raw_output_preview",
        "raw_output_preview_truncated",
        "raw_output_preview_max_chars",
        "raw_output_sha256",
        "validation_errors",
    }
    return {key: details[key] for key in keys if key in details}


def _sanitized_preview(text: str, *, max_chars: int) -> tuple[str, bool]:
    normalized = re.sub(r"\s+", " ", text).strip()
    sanitized = _redact_secret_like_values(normalized)
    if len(sanitized) <= max_chars:
        return sanitized, False
    return sanitized[:max_chars], True


def _redact_secret_like_values(text: str) -> str:
    redacted = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}\b", "sk-[redacted]", text)
    redacted = re.sub(
        r"(?i)\b(bearer|api[_-]?key|token|secret|password)\s*[:=]\s*['\"]?[^'\"\s,}]+",
        r"\1=[redacted]",
        redacted,
    )
    return redacted


def _compact_planner_example_json() -> str:
    return json.dumps(
        {
            "intent": "definition_how_it_works",
            "normalized_question": "What is Example and how does it work?",
            "subquestions": ["What is Example?", "How does Example work?"],
            "search_queries": [
                {
                    "query_text": "Example official documentation",
                    "rationale": "Find the official definition.",
                    "expected_source_type": "official_docs",
                    "priority": 1,
                },
                {
                    "query_text": "Example reference overview",
                    "rationale": "Find stable reference context.",
                    "expected_source_type": "reference",
                    "priority": 2,
                },
            ],
            "source_preferences": {
                "preferred_domains": [],
                "avoid_domains": ["reddit.com"],
                "freshness_required": False,
            },
            "answer_outline": ["Definition", "Mechanism"],
            "risk_notes": ["Prefer official sources."],
            "planner_mode": "llm",
            "warnings": [],
        },
        separators=(",", ":"),
    )


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
    guarded_plan = _apply_research_plan_guardrails(
        plan,
        query=query,
        max_search_queries=max_search_queries,
    )
    planner_diagnostics = payload.get("planner_diagnostics")
    if isinstance(planner_diagnostics, dict) and planner_diagnostics:
        return _plan_with_updates(guarded_plan, diagnostics=dict(planner_diagnostics))
    return guarded_plan


def _apply_research_plan_guardrails(
    plan: ResearchPlan,
    *,
    query: str,
    max_search_queries: int,
) -> ResearchPlan:
    intent_classification = _classify_research_intent(query=query, intent=plan.intent)
    recent_nvidia_open_model_query = _is_recent_nvidia_open_model_query(query)
    overview_query = (
        intent_classification == "overview_definition_intent"
        and not recent_nvidia_open_model_query
    )
    deployment_query = intent_classification == "deployment_intent"
    extracted_entity = _extract_entity_from_overview_query(query) if overview_query else None
    searxng_query = _is_searxng_query(query)
    source_preferences = dict(plan.source_preferences)
    warnings = list(plan.warnings)
    guardrail_warnings: list[str] = []
    raw_planner_queries = [item.to_payload() for item in plan.search_queries]
    overridden_avoid_domains: list[str] = []
    source_preference_diagnostics: list[dict[str, Any]] = []
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

    if overview_query and extracted_entity and _is_langgraph_entity(extracted_entity):
        preferred_domains = _string_list(source_preferences.get("preferred_domains"))
        (
            source_preferences["preferred_domains"],
            source_preference_diagnostics,
        ) = _langgraph_preferred_domains_with_guardrails(preferred_domains)
        secondary_domains = _langgraph_secondary_domains(preferred_domains)
        if secondary_domains:
            source_preferences["secondary_preferred_domains"] = secondary_domains
        if source_preference_diagnostics:
            source_preferences["planner_domain_corrections"] = source_preference_diagnostics
        warning = "planner_preferred_domains_supplemented_for_langgraph_owned_sources"
        guardrail_warnings.append(warning)
        warnings.append(warning)

    search_queries = plan.search_queries
    answer_outline = list(plan.answer_outline)
    risk_notes = list(plan.risk_notes)
    guarded_intent = "deployment" if deployment_query else plan.intent
    if deployment_query and plan.intent != "deployment":
        override_warning = "planner_intent_overridden_for_deployment_query"
        guardrail_warnings.append(override_warning)
        warnings.append(override_warning)

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
        if _is_langgraph_entity(extracted_entity):
            guardrail_queries = _inject_langgraph_guardrail_queries(
                extracted_entity,
                query,
                guardrail_queries,
            )

        search_queries, dropped_or_downweighted = _merge_and_rank_planned_queries(
            guardrail_queries,
            plan.search_queries,
            max_search_queries=max_search_queries,
            demote_architecture_or_setup=not _query_explicitly_asks_admin_or_setup(query),
        )
    elif deployment_query:
        subject = _subject_from_query(query)
        guardrail_queries = [
            PlannedSearchQuery(
                query_text=query,
                rationale="Preserve the user's original deployment question.",
                expected_source_type="general_web",
                priority=1,
                query_source="original_user_query",
            ),
            PlannedSearchQuery(
                query_text=f"{subject} Docker deployment official documentation",
                rationale="Prioritize official Docker deployment guidance.",
                expected_source_type="official_installation_admin",
                priority=2,
                query_source="guardrail_query",
            ),
            PlannedSearchQuery(
                query_text=f"{subject} docker compose installation configuration settings",
                rationale="Prioritize required container configuration and setup steps.",
                expected_source_type="official_docs",
                priority=3,
                query_source="guardrail_query",
            ),
            PlannedSearchQuery(
                query_text=f"{subject} deployment environment variables secrets reverse proxy",
                rationale="Prioritize operational configuration and exposure requirements.",
                expected_source_type="official_docs",
                priority=4,
                query_source="guardrail_query",
            ),
        ]
        search_queries, dropped_or_downweighted = _merge_and_rank_planned_queries(
            guardrail_queries,
            plan.search_queries,
            max_search_queries=max_search_queries,
            demote_architecture_or_setup=False,
        )
        answer_outline = _prepend_unique_strings(
            [
                "Deployment target",
                "Deployment steps",
                "Configuration",
                "Operational limitations",
            ],
            answer_outline,
        )
        risk_notes = _prepend_unique_strings(
            [
                "Prefer official installation and operations documentation.",
                (
                    "Treat secrets, public exposure, storage, and reverse-proxy settings as "
                    "operational risk areas."
                ),
            ],
            risk_notes,
        )
    elif recent_nvidia_open_model_query:
        current_year = _current_year()
        preferred_domains = _string_list(source_preferences.get("preferred_domains"))
        source_preferences["preferred_domains"] = _prepend_unique_strings(
            [
                "nvidia.com",
                "blogs.nvidia.com",
                "developer.nvidia.com",
                "nvidianews.nvidia.com",
                "huggingface.co/nvidia",
                "github.com/NVIDIA",
            ],
            preferred_domains,
        )
        guardrail_queries = [
            PlannedSearchQuery(
                query_text=query,
                rationale="Preserve the user's original time-sensitive NVIDIA research question.",
                expected_source_type="general_web",
                priority=1,
                query_source="original_user_query",
            ),
            PlannedSearchQuery(
                query_text=(
                    f"NVIDIA open source model release last 30 days {current_year} "
                    "site:nvidia.com"
                ),
                rationale="Force current official NVIDIA release material into the plan.",
                expected_source_type="official_docs",
                priority=2,
                query_source="guardrail_query",
            ),
            PlannedSearchQuery(
                query_text=(
                    f"NVIDIA open weights model {current_year} site:blogs.nvidia.com"
                ),
                rationale="Prioritize NVIDIA official blog posts about open model releases.",
                expected_source_type="official_about",
                priority=3,
                query_source="guardrail_query",
            ),
            PlannedSearchQuery(
                query_text=(
                    f"NVIDIA Nemotron open model {current_year} site:developer.nvidia.com"
                ),
                rationale="Prioritize NVIDIA developer material for model details and impact.",
                expected_source_type="official_or_reference",
                priority=4,
                query_source="guardrail_query",
            ),
            PlannedSearchQuery(
                query_text=(
                    f"NVIDIA open AI model {current_year} site:nvidianews.nvidia.com"
                ),
                rationale="Prioritize NVIDIA newsroom announcements for release dates.",
                expected_source_type="official_docs",
                priority=5,
                query_source="guardrail_query",
            ),
            PlannedSearchQuery(
                query_text=f"NVIDIA open models {current_year} site:huggingface.co/nvidia",
                rationale="Check NVIDIA's model hub for released open weights and adoption context.",
                expected_source_type="reference",
                priority=6,
                query_source="guardrail_query",
            ),
            PlannedSearchQuery(
                query_text=f"NVIDIA open source model GitHub release {current_year} site:github.com/NVIDIA",
                rationale="Check NVIDIA-owned GitHub repositories for recent release evidence.",
                expected_source_type="official_repository",
                priority=7,
                query_source="guardrail_query",
            ),
        ]
        search_queries, dropped_or_downweighted = _merge_and_rank_planned_queries(
            guardrail_queries,
            [_normalize_recent_query_year(item) for item in plan.search_queries],
            max_search_queries=max_search_queries,
            demote_architecture_or_setup=False,
        )
        warning = "planner_queries_supplemented_for_recent_nvidia_official_sources"
        guardrail_warnings.append(warning)
        warnings.append(warning)
        risk_notes = _prepend_unique_strings(
            [
                "Treat recency as an evidence requirement and verify announcement dates.",
                "Prefer NVIDIA-owned release, developer, model hub, and repository sources.",
            ],
            risk_notes,
        )

    final_search_queries = [item.to_payload() for item in search_queries]
    dropped_or_downweighted = [
        *source_preference_diagnostics,
        *dropped_or_downweighted,
    ]
    return ResearchPlan(
        intent=guarded_intent,
        normalized_question=plan.normalized_question,
        subquestions=plan.subquestions,
        search_queries=search_queries,
        source_preferences=source_preferences,
        answer_outline=answer_outline,
        risk_notes=risk_notes,
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


def _is_langgraph_entity(entity: str | None) -> bool:
    return _compact_identifier(entity or "") == "langgraph"


def _compact_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _langgraph_preferred_domains_with_guardrails(
    preferred_domains: list[str],
) -> tuple[list[str], list[dict[str, Any]]]:
    diagnostics: list[dict[str, Any]] = []
    strong_supplements: list[str] = []
    normal_supplements: list[str] = []
    weak_supplements: list[str] = []

    for domain in preferred_domains:
        normalized = _normalize_domain_preference(domain)
        if not normalized:
            continue
        if normalized == _normalize_domain_preference(LANGGRAPH_BROAD_GITHUB_PREFERENCE):
            strong_supplements.append(LANGGRAPH_UPSTREAM_GITHUB_PREFERENCE)
            normal_supplements.append(domain)
            diagnostics.append(
                {
                    "domain": domain,
                    "query_text": domain,
                    "action": "domain_supplemented",
                    "corrected_to": LANGGRAPH_UPSTREAM_GITHUB_PREFERENCE,
                    "downrank_reason": "broad_langchain_github_supplemented_with_langgraph_repo",
                    "query_source": "planner_source_preference",
                }
            )
            continue
        if any(
            _domain_preference_matches(normalized, weak)
            for weak in LANGGRAPH_WEAK_SECONDARY_DOMAINS
        ):
            weak_supplements.append(domain)
            diagnostics.append(
                {
                    "domain": domain,
                    "query_text": domain,
                    "action": "domain_downweighted",
                    "downrank_reason": "weak_langgraph_domain_marked_secondary",
                    "query_source": "planner_source_preference",
                }
            )
            continue
        normal_supplements.append(domain)

    preferred = _prepend_unique_strings(
        list(LANGGRAPH_GUARDRAIL_PREFERRED_DOMAINS),
        [*strong_supplements, *normal_supplements, *weak_supplements],
    )
    return preferred, diagnostics


def _langgraph_secondary_domains(preferred_domains: list[str]) -> list[str]:
    secondary = [
        domain
        for domain in preferred_domains
        if any(
            _domain_preference_matches(_normalize_domain_preference(domain), weak)
            for weak in LANGGRAPH_WEAK_SECONDARY_DOMAINS
        )
    ]
    return _prepend_unique_strings([], secondary)


def _normalize_domain_preference(value: str) -> str:
    normalized = value.strip().lower()
    normalized = normalized.removeprefix("https://").removeprefix("http://")
    normalized = normalized.strip("/")
    return normalized.removeprefix("www.") if normalized != "www.langchain.com" else normalized


def _domain_preference_matches(value: str, expected: str) -> bool:
    normalized_expected = _normalize_domain_preference(expected)
    return value == normalized_expected or value.startswith(f"{normalized_expected}/")


def _inject_langgraph_guardrail_queries(
    entity: str,
    original_query: str,
    guardrail_queries: list[PlannedSearchQuery],
) -> list[PlannedSearchQuery]:
    return [
        guardrail_queries[0],
        guardrail_queries[1],
        PlannedSearchQuery(
            query_text=f"{entity} site:docs.langchain.com how it works",
            rationale="Force owned LangChain documentation into the initial LangGraph plan.",
            expected_source_type="official_docs",
            priority=3,
            query_source="guardrail_query",
        ),
        PlannedSearchQuery(
            query_text=f"{entity} site:reference.langchain.com how it works",
            rationale="Force owned LangChain reference docs into the initial LangGraph plan.",
            expected_source_type="reference",
            priority=4,
            query_source="guardrail_query",
        ),
        PlannedSearchQuery(
            query_text=f"{entity} site:www.langchain.com/langgraph how it works",
            rationale="Force the official LangGraph product page into the initial plan.",
            expected_source_type="official_about",
            priority=5,
            query_source="guardrail_query",
        ),
        PlannedSearchQuery(
            query_text=f"{entity} github langchain-ai langgraph",
            rationale="Force the upstream LangGraph GitHub repository into the initial plan.",
            expected_source_type="official_repository",
            priority=6,
            query_source="guardrail_query",
        ),
        PlannedSearchQuery(
            query_text=f"{entity} how it works state graph nodes edges workflow",
            rationale="Preserve deterministic mechanism terms for LangGraph overview coverage.",
            expected_source_type="official_docs",
            priority=7,
            query_source="guardrail_query",
        ),
        PlannedSearchQuery(
            query_text=f"{entity} privacy trust security human-in-the-loop data storage",
            rationale="Preserve deterministic trust and human-in-the-loop coverage terms.",
            expected_source_type="official_docs",
            priority=8,
            query_source="guardrail_query",
        ),
        *[
            PlannedSearchQuery(
                query_text=item.query_text,
                rationale=item.rationale,
                expected_source_type=item.expected_source_type,
                priority=index,
                query_source=item.query_source,
            )
            for index, item in enumerate(guardrail_queries[2:], start=9)
            if item.query_text.strip().lower() != original_query.strip().lower()
        ],
    ]


def _default_source_preferences() -> dict[str, Any]:
    return {
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


def _current_date_iso() -> str:
    return datetime.now(UTC).date().isoformat()


def _current_year() -> int:
    return datetime.now(UTC).year


def _is_recent_nvidia_open_model_query(query: str) -> bool:
    lower = query.lower()
    return (
        "nvidia" in lower
        and _is_recent_query(query)
        and any(term in lower for term in ("open source", "open-source", "opensource", "开源"))
        and any(term in lower for term in ("model", "models", "模型"))
    )


def _is_recent_query(query: str) -> bool:
    lower = query.lower()
    return any(
        term in lower
        for term in (
            "last 30 days",
            "past 30 days",
            "recent",
            "latest",
            "this month",
            "近30天",
            "最近30天",
            "近一个月",
            "最近一个月",
            "近期",
            "最新",
        )
    )


def _normalize_recent_query_year(query: PlannedSearchQuery) -> PlannedSearchQuery:
    current_year = _current_year()
    normalized_text = re.sub(
        r"\b20\d{2}\b",
        str(current_year),
        query.query_text,
    )
    return PlannedSearchQuery(
        query_text=normalized_text,
        rationale=query.rationale,
        expected_source_type=query.expected_source_type,
        priority=query.priority,
        query_source=query.query_source,
    )


def _planner_query_guardrail_penalty(query: PlannedSearchQuery) -> int:
    lower = query.query_text.lower()
    if (
        "github" in lower
        and "repository" in lower
        and "/" not in lower
        and "langchain-ai langgraph" not in lower
    ):
        return 12
    if any(term in lower for term in ("blog", "announcement", "tutorial")):
        return 12
    if any(term in lower for term in ("generic article", "use case", "use cases", "examples")):
        return 6
    if any(term in lower for term in ("architecture", "admin", "api", "developer", "dev/")):
        return 20
    if any(term in lower for term in ("install", "installation", "setup", "docker")):
        return 20
    if any(term in lower for term in ("compare", "comparison", "alternative", "vs ")):
        return 8
    return 0


def _planner_query_downrank_reason(query: PlannedSearchQuery) -> str:
    lower = query.query_text.lower()
    if "github" in lower and "repository" in lower and "/" not in lower:
        return "broad_repository_query_supplemented_by_upstream_repo"
    if any(term in lower for term in ("blog", "announcement")):
        return "blog_or_announcement_query_downweighted_for_overview"
    if "tutorial" in lower:
        return "generic_tutorial_query_downweighted_for_overview"
    if any(term in lower for term in ("generic article", "use case", "use cases", "examples")):
        return "generic_or_examples_query_lower_priority_for_overview"
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
    if _is_deployment_query(query):
        return "deployment_intent"
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
            priority_value = item.get("priority")
            priority = priority_value if isinstance(priority_value, int) else index
        else:
            continue
        if isinstance(item, str):
            query_source = "planner_query"
        dedupe_key = query_text.lower()
        if not query_text or dedupe_key in seen_query_texts:
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
        seen_query_texts.add(dedupe_key)
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


def _object_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_value(value: Any, *, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _optional_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


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
    for pattern in (
        r"(?i)^how\s+to\s+(?:deploy|install|self-host|self\s+host)\s+(.+?)(?:\s+(?:with|using|via|on)\b|$)",
        r"(?i)^deploy(?:ing)?\s+(.+?)(?:\s+(?:with|using|via|on)\b|$)",
        r"(?i)^(.+?)\s+(?:docker|container|compose)\s+deployment$",
    ):
        match = re.match(pattern, normalized)
        if match is not None:
            subject = match.group(1).strip()
            if subject:
                return subject

    lower = normalized.lower()
    if lower.startswith("what is "):
        remainder = normalized[8:].strip()
        lower_remainder = remainder.lower()
        marker = " and how"
        if marker in lower_remainder:
            return remainder[: lower_remainder.index(marker)].strip() or normalized
        return remainder or normalized
    return normalized


def _is_deployment_query(query: str) -> bool:
    lower = query.lower()
    if re.search(r"\b(deploy|deploying|deployed|deployment|self-host|self\s+host)\b", lower):
        return True
    if re.search(r"\bhow\s+to\b", lower) and re.search(
        r"\b(docker|docker-compose|compose|container|containers|install|installation)\b",
        lower,
    ):
        return True
    return False
