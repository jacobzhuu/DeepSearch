from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from json import JSONDecodeError
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from services.orchestrator.app.llm import LLMError, LLMProvider, LLMRequest
from services.orchestrator.app.planning.types import PlannedSearchQuery

STRATEGIST_PROMPT_VERSION = "llm_research_strategist_v1"


@dataclass(frozen=True)
class ResearchStrategyResult:
    status: str
    used: bool
    fallback_reason: str | None
    decision: str | None
    planned_queries: tuple[PlannedSearchQuery, ...]
    diagnostics: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "used": self.used,
            "fallback_reason": self.fallback_reason,
            "decision": self.decision,
            "planned_queries": [query.to_payload() for query in self.planned_queries],
            "diagnostics": self.diagnostics,
        }


class _StrategistQuery(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    query_text: str = Field(default="", min_length=0, max_length=240, alias="query")
    language: str | None = Field(default=None, max_length=16)
    target_slots: list[str] = Field(default_factory=list, alias="target_slot_id")
    expected_source_types: list[str] = Field(default_factory=list, alias="source_type")
    rationale: str = Field(default="No rationale provided.", max_length=500, alias="reasoning")
    priority: int = Field(default=5, ge=1, le=99)

    @field_validator("query_text", "rationale", mode="before")
    @classmethod
    def _strip_text(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        return value

    @field_validator("target_slots", "expected_source_types", mode="before")
    @classmethod
    def _ensure_list(cls, value: object) -> object:
        if isinstance(value, str):
            return [value.strip()]
        if value is None:
            return []
        return value


class _CoverageAssessment(BaseModel):
    model_config = ConfigDict(extra="allow")

    overall_status: str = Field(default="uncertain", max_length=80)
    required_slots_missing: list[str] = Field(default_factory=list)
    main_problem: str | None = Field(default=None, max_length=600)


class _SourceSelectionGuidance(BaseModel):
    model_config = ConfigDict(extra="allow")

    must_fetch_source_types: list[str] = Field(default_factory=list)
    prefer_new_domains: bool = True
    avoid_domains: list[str] = Field(default_factory=list)
    avoid_reason: str | None = Field(default=None, max_length=400)


class _MinimumEvidenceToStop(BaseModel):
    model_config = ConfigDict(extra="allow")

    required_slots_must_be_at_least: str = "moderate"
    min_distinct_domains: int = Field(default=3, ge=0, le=20)
    min_primary_or_reference_sources: int = Field(default=1, ge=0, le=20)
    allow_report_with_warning: bool = True


class _StrategistPayload(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    decision: Literal[
        "continue_search",
        "fetch_more_existing_candidates",
        "stop_sufficient",
        "stop_budget_exhausted",
        "stop_unanswerable",
    ] = Field(default="continue_search")
    decision_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    stop_reason: str | None = Field(default=None, max_length=300)
    coverage_assessment: _CoverageAssessment = Field(default_factory=_CoverageAssessment)
    next_queries: list[_StrategistQuery] = Field(default_factory=list)
    source_selection_guidance: _SourceSelectionGuidance = Field(
        default_factory=_SourceSelectionGuidance
    )
    minimum_evidence_to_stop: _MinimumEvidenceToStop = Field(default_factory=_MinimumEvidenceToStop)

    @field_validator("decision", mode="before")
    @classmethod
    def _normalize_decision(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().lower().replace(" ", "_")
        mapping = {
            "continue": "continue_search",
            "continue_search": "continue_search",
            "search": "continue_search",
            "search_more": "continue_search",
            "more": "continue_search",
            "research_more": "continue_search",
            "fetch": "fetch_more_existing_candidates",
            "fetch_more": "fetch_more_existing_candidates",
            "fetch_more_existing_candidates": "fetch_more_existing_candidates",
            "stop": "stop_sufficient",
            "done": "stop_sufficient",
            "finish": "stop_sufficient",
            "complete": "stop_sufficient",
            "stop_sufficient": "stop_sufficient",
            "sufficient": "stop_sufficient",
            "budget_exhausted": "stop_budget_exhausted",
            "stop_budget_exhausted": "stop_budget_exhausted",
            "unanswerable": "stop_unanswerable",
            "stop_unanswerable": "stop_unanswerable",
        }
        return mapping.get(normalized, value)

    @classmethod
    def model_validate(
        cls,
        obj: Any,
        *,
        strict: bool | None = None,
        from_attributes: bool | None = None,
        context: dict[str, Any] | None = None,
    ) -> _StrategistPayload:
        if not isinstance(obj, dict):
            return super().model_validate(obj, strict=strict, from_attributes=from_attributes)

        # Handle next_queries aliases
        for alias in ("search_queries", "queries", "followup_queries"):
            if alias in obj and "next_queries" not in obj:
                obj["next_queries"] = obj.pop(alias)

        # Infer decision if missing
        if "decision" not in obj:
            if obj.get("next_queries"):
                obj["decision"] = "continue_search"
            elif obj.get("stop_reason") or obj.get("overall_status") == "sufficient":
                obj["decision"] = "stop_sufficient"
            else:
                obj["decision"] = "continue_search"

        return super().model_validate(obj, strict=strict, from_attributes=from_attributes)


class LLMResearchStrategistService:
    def __init__(
        self,
        *,
        enabled: bool,
        provider: LLMProvider | None,
        model: str,
        max_queries: int,
        max_output_tokens: int,
        input_max_chars: int,
    ) -> None:
        self.enabled = enabled
        self.provider = provider
        self.model = model
        self.max_queries = max(1, max_queries)
        self.max_output_tokens = max(400, max_output_tokens)
        self.input_max_chars = max(2_000, input_max_chars)

    def decide(
        self,
        research_state: dict[str, Any],
        *,
        existing_query_texts: set[str] | None = None,
    ) -> ResearchStrategyResult:
        if not self.enabled:
            return ResearchStrategyResult("disabled", False, "disabled", None, (), {})
        if self.provider is None:
            return ResearchStrategyResult(
                "fallback",
                False,
                "provider_unavailable",
                None,
                (),
                {"reason": "LLM research strategist provider not configured."},
            )

        bounded_state = _bounded_json(research_state, self.input_max_chars)
        response_text: str | None = None
        try:
            response_text = self.provider.generate(
                LLMRequest(
                    system_prompt=_STRATEGIST_SYSTEM_PROMPT,
                    user_prompt=bounded_state,
                    model=self.model,
                    max_output_tokens=self.max_output_tokens,
                    temperature=0.0,
                    metadata={
                        "purpose": "llm_research_strategist",
                        "prompt_version": STRATEGIST_PROMPT_VERSION,
                    },
                )
            ).text
            payload = _StrategistPayload.model_validate(_parse_json_object(response_text))
        except (LLMError, JSONDecodeError, ValidationError, ValueError) as error:
            fallback_queries = _generate_deterministic_fallback_queries(
                research_state,
                existing_query_texts=existing_query_texts or set(),
                max_queries=self.max_queries,
            )
            diagnostics = _failure_diagnostics(error, raw_text=response_text)
            diagnostics["fallback_queries_generated"] = len(fallback_queries)
            return ResearchStrategyResult(
                "fallback",
                False,
                type(error).__name__,
                "continue_search" if fallback_queries else "stop_budget_exhausted",
                tuple(fallback_queries),
                diagnostics,
            )

        planned_queries = _planned_queries_from_payload(
            payload,
            existing_query_texts=existing_query_texts or set(),
            max_queries=self.max_queries,
        )
        diagnostics = {
            "prompt_version": STRATEGIST_PROMPT_VERSION,
            "decision_confidence": round(float(payload.decision_confidence), 4),
            "stop_reason": payload.stop_reason,
            "coverage_assessment": payload.coverage_assessment.model_dump(),
            "source_selection_guidance": payload.source_selection_guidance.model_dump(),
            "minimum_evidence_to_stop": payload.minimum_evidence_to_stop.model_dump(),
            "raw_next_query_count": len(payload.next_queries),
            "accepted_next_query_count": len(planned_queries),
            "input_hash": _sha256(bounded_state),
            "raw_output_hash": _sha256(response_text),
        }
        return ResearchStrategyResult(
            "used",
            True,
            None,
            payload.decision,
            tuple(planned_queries),
            diagnostics,
        )


def _generate_deterministic_fallback_queries(
    research_state: dict[str, Any],
    *,
    existing_query_texts: set[str],
    max_queries: int,
) -> list[PlannedSearchQuery]:
    coverage = research_state.get("coverage_evaluation") or {}
    missing_slots = coverage.get("required_slots_missing", [])
    weak_slots = coverage.get("required_slots_weak", [])
    if not missing_slots and not weak_slots:
        answer_slots = research_state.get("answer_slots")
        if isinstance(answer_slots, list):
            missing_slots = [
                item.get("slot_id")
                for item in answer_slots
                if isinstance(item, dict)
                and item.get("required") is True
                and item.get("status") == "missing"
            ]
            weak_slots = [
                item.get("slot_id")
                for item in answer_slots
                if isinstance(item, dict)
                and item.get("required") is True
                and item.get("status") == "weak"
            ]
    query = (
        research_state.get("query") or research_state.get("question") or "unknown research topic"
    )

    slots_to_target = [
        str(slot_id).strip()
        for slot_id in [*list(missing_slots), *list(weak_slots)]
        if str(slot_id).strip()
    ]
    if not slots_to_target:
        return []

    seen = {_normalize_query_text(item) for item in existing_query_texts}
    planned: list[PlannedSearchQuery] = []

    for slot_id in slots_to_target:
        fallback_text = f"{query} {slot_id}"
        normalized = _normalize_query_text(fallback_text)
        if normalized in seen:
            continue
        seen.add(normalized)
        planned.append(
            PlannedSearchQuery(
                query_text=fallback_text,
                rationale=f"Deterministic fallback for {slot_id} due to strategist error.",
                expected_source_type="official_or_reference",
                priority=len(planned) + 1,
                query_source="deterministic_strategist_fallback",
                metadata={"target_slots": [slot_id]},
            )
        )
        if len(planned) >= max_queries:
            break
    return planned


def _planned_queries_from_payload(
    payload: _StrategistPayload,
    *,
    existing_query_texts: set[str],
    max_queries: int,
) -> list[PlannedSearchQuery]:
    if payload.decision != "continue_search":
        return []
    seen = {_normalize_query_text(item) for item in existing_query_texts}
    planned: list[PlannedSearchQuery] = []
    for item in sorted(payload.next_queries, key=lambda query: query.priority):
        normalized = _normalize_query_text(item.query_text)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        source_types = [value for value in item.expected_source_types if value.strip()]
        planned.append(
            PlannedSearchQuery(
                query_text=item.query_text,
                rationale=item.rationale,
                expected_source_type=source_types[0] if source_types else "official_or_reference",
                priority=len(planned) + 1,
                query_source="llm_research_strategist",
                metadata={
                    "target_slots": [value for value in item.target_slots if value.strip()],
                    "language": item.language,
                    "expected_source_types": source_types,
                    "llm_strategy_decision": payload.decision,
                    "llm_strategy_confidence": round(float(payload.decision_confidence), 4),
                },
            )
        )
        if len(planned) >= max_queries:
            break
    return planned


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _extract_fenced_json(stripped)
    payload = json.loads(stripped)
    if not isinstance(payload, dict):
        raise ValueError("LLM research strategist output was not a JSON object")
    return payload


def _extract_fenced_json(text: str) -> str:
    lines = text.splitlines()
    if len(lines) < 3:
        raise ValueError("fenced JSON response was incomplete")
    first = lines[0].strip().lower()
    if first not in {"```", "```json"}:
        raise ValueError("fenced response did not start with a JSON fence")
    if lines[-1].strip() != "```":
        raise ValueError("fenced JSON response did not end with a closing fence")
    return "\n".join(lines[1:-1]).strip()


def _bounded_json(payload: dict[str, Any], max_chars: int) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def _failure_diagnostics(error: Exception, *, raw_text: str | None) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "error_type": type(error).__name__,
        "message": str(error)[:500],
        "strategist_parse_status": "failed",
        "strategist_validation_errors": (
            error.errors() if isinstance(error, ValidationError) else None
        ),
    }
    if raw_text is not None:
        diagnostics["raw_output_preview"] = raw_text[:800]
        diagnostics["raw_output_hash"] = _sha256(raw_text)
    if isinstance(error, LLMError):
        diagnostics["provider_error"] = error.to_payload()
    return diagnostics


def _normalize_query_text(value: str) -> str:
    return " ".join(value.strip().lower().split())


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


_STRATEGIST_SYSTEM_PROMPT = (
    "You are the search strategist for a grounded research pipeline. Your job is not to "
    "answer the user's question directly. Decide whether the current evidence is sufficient "
    "and, if not, generate the next search actions. Use only the provided research state. "
    "Do not invent sources or claims. Prefer queries that target missing required answer slots. "
    "Generate diverse query phrasings, including bilingual queries when useful. Avoid repeating "
    "queries that already failed unless the modified query is materially different. Prefer "
    "authoritative, primary, official, academic, or high-quality reference sources. Stop only "
    "when required answer slots have sufficient evidence or when budget is exhausted. Return "
    "valid JSON only matching this schema:\n"
    "{\n"
    '  "decision": "continue_search" | "fetch_more_existing_candidates" | '
    '"stop_sufficient" | "stop_budget_exhausted" | "stop_unanswerable",\n'
    '  "decision_confidence": 0.0 to 1.0,\n'
    '  "stop_reason": "string or null",\n'
    '  "coverage_assessment": {"overall_status": "string", '
    '"required_slots_missing": ["list"], "main_problem": "string"},\n'
    '  "next_queries": [{"query_text": "string", "language": "string or null", '
    '"target_slots": ["list"], "expected_source_types": ["list"], '
    '"rationale": "string", "priority": 1}],\n'
    '  "source_selection_guidance": {"must_fetch_source_types": ["list"], '
    '"prefer_new_domains": true, "avoid_domains": ["list"], "avoid_reason": "string"},\n'
    '  "minimum_evidence_to_stop": {"required_slots_must_be_at_least": "moderate", '
    '"min_distinct_domains": 3, "min_primary_or_reference_sources": 1, '
    '"allow_report_with_warning": true}\n'
    "}"
)
