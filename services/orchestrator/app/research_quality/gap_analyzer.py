from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SupplementalSearchQuery:
    query_text: str
    rationale: str
    expected_source_type: str
    priority: int
    slot_ids: tuple[str, ...]
    round_no: int

    def to_payload(self) -> dict[str, Any]:
        metadata = {
            "target_slots": list(self.slot_ids),
            "source_role": _source_role_for_expected_source_type(self.expected_source_type),
            "query_source": "gap_analyzer",
        }
        return {
            "query_text": self.query_text,
            "rationale": self.rationale,
            "expected_source_type": self.expected_source_type,
            "priority": self.priority,
            "slot_ids": list(self.slot_ids),
            "round_no": self.round_no,
            "query_source": "gap_analyzer",
            "metadata": metadata,
        }


@dataclass(frozen=True)
class GapAnalysisResult:
    round_no: int
    max_rounds: int
    triggered: bool
    reason: str | None
    required_slots_missing: tuple[dict[str, Any], ...]
    required_slots_weak: tuple[dict[str, Any], ...]
    supplemental_queries: tuple[SupplementalSearchQuery, ...]
    warnings: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "round_no": self.round_no,
            "max_rounds": self.max_rounds,
            "triggered": self.triggered,
            "reason": self.reason,
            "required_slots_missing": [dict(slot) for slot in self.required_slots_missing],
            "required_slots_weak": [dict(slot) for slot in self.required_slots_weak],
            "supplemental_queries": [query.to_payload() for query in self.supplemental_queries],
            "warnings": list(self.warnings),
        }


def analyze_required_slot_gaps(
    query: str,
    *,
    slot_coverage_summary: list[dict[str, Any]],
    round_no: int,
    max_rounds: int,
    max_queries_per_round: int = 4,
    existing_query_texts: set[str] | None = None,
) -> GapAnalysisResult:
    bounded_max_rounds = max(0, max_rounds)
    bounded_round_no = max(1, round_no)
    required_slots = [slot for slot in slot_coverage_summary if slot.get("required") is True]
    missing_slots = tuple(
        _slot_payload(slot) for slot in required_slots if slot.get("status") == "missing"
    )
    weak_slots = tuple(
        _slot_payload(slot) for slot in required_slots if slot.get("status") == "weak"
    )
    if bounded_max_rounds <= 0:
        return _empty_result(
            round_no=bounded_round_no,
            max_rounds=bounded_max_rounds,
            reason="gap_rounds_disabled",
            missing_slots=missing_slots,
            weak_slots=weak_slots,
        )
    if bounded_round_no > bounded_max_rounds:
        return _empty_result(
            round_no=bounded_round_no,
            max_rounds=bounded_max_rounds,
            reason="max_gap_rounds_reached",
            missing_slots=missing_slots,
            weak_slots=weak_slots,
        )
    if not missing_slots and not weak_slots:
        return _empty_result(
            round_no=bounded_round_no,
            max_rounds=bounded_max_rounds,
            reason="required_slots_covered",
        )

    existing = {item.strip() for item in existing_query_texts or set() if item.strip()}
    supplemental_queries = _build_supplemental_queries(
        query,
        missing_slots=missing_slots,
        weak_slots=weak_slots,
        round_no=bounded_round_no,
        limit=max(1, max_queries_per_round),
        existing_query_texts=existing,
    )
    warnings: list[str] = []
    if not supplemental_queries:
        warnings.append(
            "Required slots are missing or weak, but no new supplemental queries were generated."
        )

    return GapAnalysisResult(
        round_no=bounded_round_no,
        max_rounds=bounded_max_rounds,
        triggered=bool(supplemental_queries),
        reason="missing_or_weak_required_slots",
        required_slots_missing=missing_slots,
        required_slots_weak=weak_slots,
        supplemental_queries=tuple(supplemental_queries),
        warnings=tuple(warnings),
    )


def _empty_result(
    *,
    round_no: int,
    max_rounds: int,
    reason: str,
    missing_slots: tuple[dict[str, Any], ...] = (),
    weak_slots: tuple[dict[str, Any], ...] = (),
) -> GapAnalysisResult:
    warnings: tuple[str, ...] = ()
    if reason in {"gap_rounds_disabled", "max_gap_rounds_reached"} and (
        missing_slots or weak_slots
    ):
        warnings = ("Required slots remain missing or weak, but no more gap rounds are allowed.",)
    return GapAnalysisResult(
        round_no=round_no,
        max_rounds=max_rounds,
        triggered=False,
        reason=reason,
        required_slots_missing=missing_slots,
        required_slots_weak=weak_slots,
        supplemental_queries=(),
        warnings=warnings,
    )


def _slot_payload(slot: dict[str, Any]) -> dict[str, Any]:
    return {
        "slot_id": _string_value(slot.get("slot_id"), default="unknown"),
        "label": _string_value(slot.get("label"), default="Unknown"),
        "status": _string_value(slot.get("status"), default="missing"),
        "expected_claim_categories": _string_list(slot.get("expected_claim_categories")),
        "supported_claim_count": _int_value(slot.get("supported_claim_count")),
        "weak_supported_claim_count": _int_value(slot.get("weak_supported_claim_count")),
        "accepted_evidence_count": _int_value(slot.get("accepted_evidence_count")),
    }


def _build_supplemental_queries(
    query: str,
    *,
    missing_slots: tuple[dict[str, Any], ...],
    weak_slots: tuple[dict[str, Any], ...],
    round_no: int,
    limit: int,
    existing_query_texts: set[str],
) -> list[SupplementalSearchQuery]:
    normalized_query = " ".join(query.split())
    gap_slots = [*missing_slots, *weak_slots]
    supplemental: list[SupplementalSearchQuery] = []
    seen = set(existing_query_texts)

    for targeted_query in _targeted_project_queries(
        normalized_query,
        gap_slots=gap_slots,
        round_no=round_no,
        limit=limit,
        existing_query_texts=seen,
    ):
        seen.add(targeted_query.query_text)
        supplemental.append(targeted_query)

    for slot in gap_slots:
        if len(supplemental) >= limit:
            break
        slot_id = _string_value(slot.get("slot_id"), default="unknown")
        selected_variant = None
        for query_suffix, source_type in _query_variants_for_slot(slot_id, slot):
            query_text = f"{normalized_query} {query_suffix}".strip()
            if query_text in seen:
                continue
            selected_variant = (query_text, source_type)
            break
        if selected_variant is None:
            continue
        query_text, source_type = selected_variant
        seen.add(query_text)
        supplemental.append(
            SupplementalSearchQuery(
                query_text=query_text,
                rationale=(
                    f"Fill required answer slot {slot_id!r} after verified coverage was "
                    f"{slot.get('status') or 'missing'}."
                ),
                expected_source_type=source_type,
                priority=len(supplemental) + 1,
                slot_ids=(slot_id,),
                round_no=round_no,
            )
        )
    return supplemental


def _targeted_project_queries(
    query: str,
    *,
    gap_slots: list[dict[str, Any]],
    round_no: int,
    limit: int,
    existing_query_texts: set[str],
) -> list[SupplementalSearchQuery]:
    if limit <= 0 or not gap_slots:
        return []
    project = _targeted_project_for_query(query)
    if project is None:
        return []
    slot_ids = tuple(
        dict.fromkeys(_string_value(slot.get("slot_id"), default="unknown") for slot in gap_slots)
    )
    targeted_queries = {
        "langgraph": _langgraph_targeted_queries_for_slots(slot_ids),
        "claude": (
            ("Claude site:anthropic.com", "official_docs"),
            ("Claude site:docs.anthropic.com", "official_docs"),
            ("Claude site:blog.anthropic.com", "official_docs"),
            ("Claude API release notes", "official_docs"),
            ("Claude model versions performance", "official_or_reference"),
        ),
        "anthropic": (
            ("Anthropic site:anthropic.com", "official_docs"),
            ("Anthropic news announcements", "official_docs"),
            ("Anthropic Claude updates", "official_docs"),
        ),
    }.get(project, ())
    supplemental: list[SupplementalSearchQuery] = []
    seen = set(existing_query_texts)
    for query_text, source_type in targeted_queries:
        if len(supplemental) >= limit:
            break
        if query_text in seen:
            continue
        seen.add(query_text)
        supplemental.append(
            SupplementalSearchQuery(
                query_text=query_text,
                rationale=(
                    "Search owned official/reference sources before secondary mirrors "
                    f"while filling required answer slots {', '.join(slot_ids)}."
                ),
                expected_source_type=source_type,
                priority=len(supplemental) + 1,
                slot_ids=slot_ids,
                round_no=round_no,
            )
        )
    return supplemental


def _langgraph_targeted_queries_for_slots(slot_ids: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    by_slot: dict[str, tuple[tuple[str, str], ...]] = {
        "definition": (("LangGraph official documentation overview", "official_docs"),),
        "motivation_problem": (
            ("LangGraph why use LangGraph reliable agents official docs", "official_docs"),
        ),
        "core_abstractions": (
            ("LangGraph site:docs.langchain.com concepts state graph nodes edges", "official_docs"),
            (
                "LangGraph site:reference.langchain.com StateGraph graph state reference",
                "reference",
            ),
        ),
        "architecture": (
            ("LangGraph architecture runtime graph workflow official docs", "official_docs"),
        ),
        "execution_model": (
            (
                "LangGraph execution model durable execution checkpoints streaming official docs",
                "official_docs",
            ),
        ),
        "workflow_lifecycle": (
            (
                "LangGraph workflow lifecycle threads persistence human-in-the-loop docs",
                "official_docs",
            ),
        ),
        "official_sources": (
            ("LangGraph site:docs.langchain.com", "official_docs"),
            ("LangGraph site:reference.langchain.com", "reference"),
            ("LangGraph github langchain-ai langgraph README", "official_repository"),
        ),
    }
    queries: list[tuple[str, str]] = []
    for slot_id in slot_ids:
        queries.extend(by_slot.get(slot_id, ()))
    queries.extend(
        (
            ("LangGraph official documentation overview", "official_docs"),
            (
                "LangGraph site:reference.langchain.com StateGraph graph state reference",
                "reference",
            ),
            ("LangGraph github langchain-ai langgraph README", "official_repository"),
            (
                "LangGraph examples agents workflows human-in-the-loop official docs",
                "official_docs",
            ),
        )
    )
    return tuple(dict.fromkeys(queries))


def _targeted_project_for_query(query: str) -> str | None:
    normalized = query.lower()
    if "langgraph" in normalized:
        return "langgraph"
    if "claude" in normalized:
        return "claude"
    if "anthropic" in normalized:
        return "anthropic"
    return None


def _query_variants_for_slot(slot_id: str, slot: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    slot_id = slot_id.strip().lower()
    categories = set(_string_list(slot.get("expected_claim_categories")))
    if slot_id.startswith("deployment"):
        if "configuration" in slot_id:
            return (
                (
                    "configuration environment variables docker compose official docs",
                    "official_docs",
                ),
                (
                    "settings environment variables configuration reference official docs",
                    "official_docs",
                ),
                ("docker compose env config examples official documentation", "official_docs"),
            )
        if "steps" in slot_id:
            return (
                ("installation deployment steps docker compose official docs", "official_docs"),
                (
                    "deployment guide quickstart docker compose official documentation",
                    "official_docs",
                ),
                ("self hosted setup tutorial production steps official docs", "official_docs"),
            )
        if "target" in slot_id:
            return (
                ("deployment target self hosted docker official docs", "official_docs"),
                (
                    "supported deployment options self hosting official documentation",
                    "official_docs",
                ),
                ("deployment architecture host requirements official docs", "official_docs"),
            )
        return (
            ("deployment docker self hosting official documentation", "official_docs"),
            ("deployment guide installation configuration official docs", "official_docs"),
            ("self hosted operations limitations official documentation", "official_docs"),
        )
    if "privacy" in slot_id or "privacy" in categories:
        return (
            ("privacy model limitations official documentation", "official_or_reference"),
            (
                "tracking data collection privacy policy technical documentation",
                "official_or_reference",
            ),
            ("privacy advantages limitations reference documentation", "official_or_reference"),
        )
    if "motivation" in slot_id or "problem" in slot_id:
        return (
            ("motivation problem solved official documentation", "official_docs"),
            ("why use architecture overview official docs", "official_docs"),
            ("use cases positioning official documentation", "official_or_reference"),
        )
    if "core_abstractions" in slot_id:
        return (
            ("core concepts abstractions API reference official docs", "official_docs"),
            ("state graph nodes edges reference documentation", "official_or_reference"),
            ("github README core concepts examples", "official_repository"),
        )
    if "architecture" in slot_id:
        return (
            ("architecture runtime components official documentation", "official_docs"),
            ("technical overview architecture reference documentation", "official_or_reference"),
            ("system design graph workflow official docs", "official_docs"),
        )
    if "execution_model" in slot_id:
        return (
            ("execution model runtime scheduling checkpoints official docs", "official_docs"),
            ("durable execution streaming resume reference documentation", "official_or_reference"),
            ("how it executes workflow runtime official docs", "official_docs"),
        )
    if "workflow_lifecycle" in slot_id:
        return (
            ("workflow lifecycle threads persistence official docs", "official_docs"),
            ("human in the loop workflow lifecycle official documentation", "official_docs"),
            (
                "state transitions workflow examples reference documentation",
                "official_or_reference",
            ),
        )
    if "key_features" in slot_id:
        return (
            ("key features capabilities official documentation", "official_docs"),
            ("features integrations streaming checkpointing official docs", "official_docs"),
            ("changelog release notes official blog", "official_docs"),
        )
    if "examples" in slot_id or "use_cases" in slot_id:
        return (
            ("examples use cases official documentation", "official_docs"),
            ("github README examples tutorials official repository", "official_repository"),
            ("applications agents workflows examples", "official_or_reference"),
        )
    if "limitations" in slot_id:
        return (
            ("limitations caveats tradeoffs official documentation", "official_or_reference"),
            ("known limitations troubleshooting reference documentation", "official_or_reference"),
            ("comparison alternatives limitations", "official_or_reference"),
        )
    if "official_sources" in slot_id:
        return (
            ("official documentation overview", "official_docs"),
            ("API reference official documentation", "reference"),
            ("GitHub README official repository", "official_repository"),
        )
    if "mechanism" in slot_id or "mechanism" in categories:
        return (
            ("how it works architecture official documentation", "official_or_reference"),
            ("technical overview components workflow official docs", "official_or_reference"),
            ("architecture mechanism design reference documentation", "official_or_reference"),
        )
    if "comparison" in slot_id:
        return (
            ("comparison tradeoffs limitations official documentation", "official_or_reference"),
            ("differences features limitations vendor documentation", "official_or_reference"),
            ("evaluation criteria strengths weaknesses reference sources", "official_or_reference"),
        )
    if "definition" in slot_id or "definition" in categories or "overview" in slot_id:
        return (
            ("overview definition official documentation", "official_or_reference"),
            ("what it is introduction reference documentation", "official_or_reference"),
            ("project overview official about documentation", "official_or_reference"),
        )
    label = _string_value(slot.get("label"), default=slot_id)
    return (
        (f"{label} official documentation", "official_or_reference"),
        (f"{label} reference guide", "official_or_reference"),
        (f"{label} limitations details", "official_or_reference"),
    )


def _source_role_for_expected_source_type(source_type: str) -> str:
    if source_type in {"official_repository", "github_readme_or_repo"}:
        return "official_repository"
    if source_type == "reference":
        return "official_reference"
    if source_type.startswith("official"):
        return "official_docs"
    return "high_quality_secondary_reference"


def _string_value(value: Any, *, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


def _int_value(value: Any) -> int:
    if isinstance(value, int):
        return value
    return 0
