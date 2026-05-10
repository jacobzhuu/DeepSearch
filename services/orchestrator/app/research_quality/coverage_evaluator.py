from __future__ import annotations

from dataclasses import dataclass
from typing import Any

COVERAGE_STATUS_ORDER: dict[str, int] = {
    "missing": 0,
    "weak": 1,
    "moderate": 2,
    "covered": 2,
    "strong": 3,
}


@dataclass(frozen=True)
class CoverageEvaluation:
    overall_status: str
    required_slot_min_status: str
    required_slots_total: int
    required_slots_sufficient: int
    required_slots_missing: tuple[str, ...]
    required_slots_weak: tuple[str, ...]
    required_slots_conflicted: tuple[str, ...]
    distinct_domains: int
    authoritative_sources: int
    source_roles: int
    min_distinct_domains: int
    min_authoritative_sources: int
    min_source_roles: int
    can_stop: bool
    stop_reason: str
    required_slots_blocked: tuple[str, ...] = ()
    required_slots_underprocessed: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "overall_status": self.overall_status,
            "required_slot_min_status": self.required_slot_min_status,
            "required_slots_total": self.required_slots_total,
            "required_slots_sufficient": self.required_slots_sufficient,
            "required_slots_missing": list(self.required_slots_missing),
            "required_slots_weak": list(self.required_slots_weak),
            "required_slots_conflicted": list(self.required_slots_conflicted),
            "required_slots_blocked": list(self.required_slots_blocked),
            "required_slots_underprocessed": list(self.required_slots_underprocessed),
            "distinct_domains": self.distinct_domains,
            "authoritative_sources": self.authoritative_sources,
            "source_roles": self.source_roles,
            "min_distinct_domains": self.min_distinct_domains,
            "min_authoritative_sources": self.min_authoritative_sources,
            "min_source_roles": self.min_source_roles,
            "can_stop": self.can_stop,
            "stop_reason": self.stop_reason,
            "warnings": list(self.warnings),
        }


def evaluate_research_coverage(
    *,
    slot_coverage_summary: list[dict[str, Any]],
    source_yield_summary: list[dict[str, Any]] | None = None,
    required_slot_min_status: str = "moderate",
    min_distinct_domains: int = 3,
    min_authoritative_sources: int = 1,
    min_source_roles: int = 2,
    allow_low_coverage_report: bool = True,
    budget_exhausted: bool = False,
) -> CoverageEvaluation:
    min_status = _normalize_min_status(required_slot_min_status)
    min_score = COVERAGE_STATUS_ORDER[min_status]
    required_slots = [slot for slot in slot_coverage_summary if slot.get("required") is True]
    missing: list[str] = []
    weak: list[str] = []
    conflicted: list[str] = []
    blocked: list[str] = []
    underprocessed: list[str] = []
    sufficient = 0

    source_summary = source_yield_summary or []

    for slot in required_slots:
        slot_id = _slot_id(slot)
        status = _normalized_slot_status(slot)
        if status == "conflicted":
            conflicted.append(slot_id)
            continue

        score = COVERAGE_STATUS_ORDER.get(status, 0)
        if score >= min_score:
            sufficient += 1
        elif status == "missing":
            # Check if this slot was blocked or underprocessed
            is_blocked = _is_slot_blocked(slot_id, source_summary)
            is_underprocessed = _is_slot_underprocessed(slot_id, source_summary)

            if is_blocked:
                blocked.append(slot_id)
            elif is_underprocessed:
                underprocessed.append(slot_id)
            else:
                missing.append(slot_id)
        else:
            weak.append(slot_id)

    distinct_domains = _distinct_domain_count(source_summary)
    authoritative_sources = _authoritative_source_count(source_summary)
    source_roles = _source_role_diversity_count(source_summary)

    slot_coverage_ok = bool(required_slots) and sufficient == len(required_slots) and not conflicted
    source_diversity_ok = distinct_domains >= max(0, min_distinct_domains)
    authoritative_ok = authoritative_sources >= max(0, min_authoritative_sources)
    role_diversity_ok = source_roles >= max(0, min_source_roles)

    can_stop = slot_coverage_ok and source_diversity_ok and authoritative_ok and role_diversity_ok

    warnings: list[str] = []
    if not source_diversity_ok:
        warnings.append("source_diversity_below_threshold")
    if not authoritative_ok:
        warnings.append("authoritative_source_count_below_threshold")
    if not role_diversity_ok:
        warnings.append("source_role_diversity_below_threshold")
    if conflicted:
        warnings.append("required_slots_have_conflicts")
    if blocked:
        warnings.append("authoritative_sources_blocked")

    if can_stop:
        overall_status = "sufficient"
        stop_reason = "coverage_sufficient"
    elif budget_exhausted:
        has_any_evidence = sufficient > 0 or len(weak) > 0
        if not has_any_evidence and not conflicted:
            overall_status = "insufficient"
            stop_reason = "coverage_failed_no_evidence"
        else:
            overall_status = (
                "budget_exhausted_partial" if allow_low_coverage_report else "insufficient"
            )
            stop_reason = "coverage_partial_budget_exhausted"
    elif conflicted:
        overall_status = "conflicted"
        stop_reason = "required_slots_conflicted"
    elif not required_slots:
        overall_status = "insufficient"
        stop_reason = "no_required_slots_available"
    else:
        overall_status = "insufficient"
        stop_reason = "required_slots_below_threshold"

    return CoverageEvaluation(
        overall_status=overall_status,
        required_slot_min_status=min_status,
        required_slots_total=len(required_slots),
        required_slots_sufficient=sufficient,
        required_slots_missing=tuple(missing),
        required_slots_weak=tuple(weak),
        required_slots_conflicted=tuple(conflicted),
        required_slots_blocked=tuple(blocked),
        required_slots_underprocessed=tuple(underprocessed),
        distinct_domains=distinct_domains,
        authoritative_sources=authoritative_sources,
        source_roles=source_roles,
        min_distinct_domains=max(0, min_distinct_domains),
        min_authoritative_sources=max(0, min_authoritative_sources),
        min_source_roles=max(0, min_source_roles),
        can_stop=can_stop,
        stop_reason=stop_reason,
        warnings=tuple(warnings),
    )


def _is_slot_blocked(slot_id: str, source_yield_summary: list[dict[str, Any]]) -> bool:
    # A slot is blocked if authoritative sources for it failed with 403 or blocked reason
    for source in source_yield_summary:
        if slot_id not in _get_source_slots(source):
            continue
        if source.get("dropped_reasons") and "blocked_by_policy" in source["dropped_reasons"]:
            return True
    return False


def _is_slot_underprocessed(slot_id: str, source_yield_summary: list[dict[str, Any]]) -> bool:
    # A slot is underprocessed if snapshots exist for it but weren't parsed or didn't yield claims due to limits
    for source in source_yield_summary:
        if slot_id not in _get_source_slots(source):
            continue
        if source.get("fetched") is True and not source.get("parsed"):
            return True
    return False


def _get_source_slots(source: dict[str, Any]) -> list[str]:
    # Use target_slot_ids if available (populated by the pipeline)
    slots = source.get("target_slot_ids")
    if isinstance(slots, list):
        return [str(s) for s in slots if s]
    return []


def _source_role_diversity_count(source_yield_summary: list[dict[str, Any]]) -> int:
    roles = {
        str(item.get("source_intent") or item.get("source_category") or "").strip()
        for item in source_yield_summary
        if (
            item.get("fetched") is True
            or item.get("parsed") is True
            or item.get("contribution_level") in {"high", "medium"}
        )
    }
    return len({role for role in roles if role})


def _normalize_min_status(value: str) -> str:
    normalized = value.strip().lower() if isinstance(value, str) else "moderate"
    if normalized == "covered":
        return "moderate"
    if normalized not in {"missing", "weak", "moderate", "strong"}:
        return "moderate"
    return normalized


def _normalized_slot_status(slot: dict[str, Any]) -> str:
    status = str(slot.get("status") or "missing").strip().lower()
    if status in {"conflict", "conflicted"}:
        return "conflicted"
    supported = _int_value(slot.get("supported_claim_count"))
    weak_supported = _int_value(slot.get("weak_supported_claim_count"))
    unsupported = _int_value(slot.get("unsupported_claim_count"))
    source_count = _int_value(slot.get("source_count"))
    accepted_evidence = _int_value(slot.get("accepted_evidence_count"))

    if supported > 0 and unsupported > 0 and status in {"mixed", "conflicted"}:
        return "conflicted"
    if status == "covered":
        if supported >= 2 or source_count >= 2:
            return "strong"
        if accepted_evidence < 3 and source_count < 2:
            return "weak"
        return "moderate"
    if supported > 0:
        if accepted_evidence < 3 and source_count < 2:
            return "weak"
        return "moderate"
    if status == "weak":
        return "weak"
    if weak_supported > 0 or accepted_evidence > 0:
        return "weak"
    return "missing"


def _slot_id(slot: dict[str, Any]) -> str:
    value = slot.get("slot_id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "unknown"


def _distinct_domain_count(source_yield_summary: list[dict[str, Any]]) -> int:
    domains = {
        value.strip().lower()
        for item in source_yield_summary
        for value in [_domain_value(item)]
        if value
        and (
            item.get("fetched") is True
            or item.get("parsed") is True
            or item.get("contribution_level") in {"high", "medium"}
        )
    }
    return len(domains)


def _authoritative_source_count(source_yield_summary: list[dict[str, Any]]) -> int:
    count = 0
    for item in source_yield_summary:
        if not (
            item.get("fetched") is True
            or item.get("parsed") is True
            or item.get("contribution_level") in {"high", "medium"}
        ):
            continue
        source_intent = str(item.get("source_intent") or item.get("source_category") or "").strip()
        if source_intent.startswith("official") or source_intent in {
            "primary_reference",
            "reference",
            "wikipedia_reference",
            "github_readme_or_repo",
        }:
            count += 1
    return count


def _domain_value(item: dict[str, Any]) -> str | None:
    value = item.get("domain")
    if isinstance(value, str) and value.strip():
        return value.strip()
    url = item.get("canonical_url") or item.get("url")
    if not isinstance(url, str) or "://" not in url:
        return None
    host = url.split("://", 1)[1].split("/", 1)[0].strip().lower()
    return host or None


def _int_value(value: object) -> int:
    return value if isinstance(value, int) else 0
