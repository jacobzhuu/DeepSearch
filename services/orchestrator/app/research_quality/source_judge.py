from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from packages.db.models import CandidateUrl
from services.orchestrator.app.llm.providers import LLMProvider
from services.orchestrator.app.llm.types import LLMError, LLMRequest
from services.orchestrator.app.research_quality.source_intent import source_intent_metadata

SOURCE_JUDGE_PROMPT_VERSION = "source_judge_shadow_v1"
SOURCE_JUDGE_ALLOWED_LABELS = {
    "accept",
    "authoritative",
    "downrank",
    "relevant",
    "reject",
    "stale",
    "low_quality",
    "marketing",
    "duplicate",
    "unsafe",
    "uncertain",
}
SOURCE_TRIAGE_DECISIONS = {
    "must_fetch",
    "fetch_if_budget_allows",
    "defer",
    "skip_duplicate",
    "skip_low_value",
    "skip_unsafe_or_invalid",
}


@dataclass(frozen=True)
class SourceJudgeResult:
    candidate_url_id: str
    canonical_url: str
    provider: str
    model: str
    prompt_version: str
    input_summary: dict[str, Any]
    output_judgment: dict[str, Any]
    confidence: float
    reasons: list[str]
    fallback_status: str
    used_in_final_ranking: bool

    def to_payload(self) -> dict[str, Any]:
        return {
            "candidate_url_id": self.candidate_url_id,
            "canonical_url": self.canonical_url,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "input_summary": self.input_summary,
            "output_judgment": self.output_judgment,
            "confidence": self.confidence,
            "reasons": self.reasons,
            "fallback_status": self.fallback_status,
            "used_in_final_ranking": self.used_in_final_ranking,
        }


class SourceJudgeService:
    def __init__(
        self,
        *,
        enabled: bool,
        active_rerank: bool,
        provider: LLMProvider | None,
        model: str,
        max_candidates: int,
        active_triage: bool = False,
    ) -> None:
        self.enabled = enabled
        self.active_rerank = active_rerank
        self.active_triage = active_triage
        self.provider = provider
        self.model = model
        self.max_candidates = max(1, max_candidates)

    def judge_candidates(
        self,
        candidates: list[CandidateUrl],
        *,
        query: str,
    ) -> list[SourceJudgeResult]:
        selected_candidates = candidates[: self.max_candidates]
        if not self.enabled:
            return [
                self._fallback_result(
                    candidate,
                    query=query,
                    fallback_status="disabled",
                    reason="LLM source judge disabled.",
                )
                for candidate in selected_candidates
            ]
        if self.provider is None:
            return [
                self._fallback_result(
                    candidate,
                    query=query,
                    fallback_status="provider_unavailable",
                    reason="LLM source judge provider not configured.",
                )
                for candidate in selected_candidates
            ]

        results: list[SourceJudgeResult] = []
        for candidate in selected_candidates:
            results.append(self._judge_one(candidate, query=query))
        return results

    def _judge_one(self, candidate: CandidateUrl, *, query: str) -> SourceJudgeResult:
        input_summary = _candidate_input_summary(
            candidate,
            query=query,
            active_rerank=self.active_rerank,
        )
        provider = self.provider
        if provider is None:
            return self._fallback_result(
                candidate,
                query=query,
                fallback_status="provider_unavailable",
                reason="LLM source judge provider not configured.",
            )
        try:
            response = provider.generate(
                LLMRequest(
                    system_prompt=_SOURCE_JUDGE_SYSTEM_PROMPT,
                    user_prompt=json.dumps(input_summary, sort_keys=True),
                    model=self.model,
                    max_output_tokens=700,
                    temperature=0.0,
                    metadata={
                        "task": "source_judge",
                        "prompt_version": SOURCE_JUDGE_PROMPT_VERSION,
                    },
                )
            )
            parsed = _parse_source_judge_output(response.text)
        except (LLMError, ValueError, json.JSONDecodeError) as error:
            return self._fallback_result(
                candidate,
                query=query,
                fallback_status="llm_failed",
                reason=f"{type(error).__name__}: {error}",
            )

        return SourceJudgeResult(
            candidate_url_id=str(candidate.id),
            canonical_url=candidate.canonical_url,
            provider=getattr(provider, "name", "unknown"),
            model=self.model,
            prompt_version=SOURCE_JUDGE_PROMPT_VERSION,
            input_summary=input_summary,
            output_judgment=parsed,
            confidence=float(parsed["confidence"]),
            reasons=list(parsed["reasons"]),
            fallback_status="none",
            used_in_final_ranking=(self.active_rerank or self.active_triage)
            and _judgment_can_affect_ranking(parsed),
        )

    def _fallback_result(
        self,
        candidate: CandidateUrl,
        *,
        query: str,
        fallback_status: str,
        reason: str,
    ) -> SourceJudgeResult:
        input_summary = _candidate_input_summary(
            candidate,
            query=query,
            active_rerank=self.active_rerank,
        )
        output_judgment = {
            "label": "uncertain",
            "confidence": 0.0,
            "reasons": [reason],
            "priority_adjustment": 0.0,
            "source_type": "unknown",
            "topic_fit": "unknown",
            "authority": "unknown",
            "novelty": "unknown",
            "expected_covered_slots": [],
            "source_role": "unknown",
            "triage_decision": "defer",
            "fetch_priority": 50,
            "risk_flags": [],
        }
        return SourceJudgeResult(
            candidate_url_id=str(candidate.id),
            canonical_url=candidate.canonical_url,
            provider=getattr(self.provider, "name", "deterministic"),
            model=self.model or "none",
            prompt_version=SOURCE_JUDGE_PROMPT_VERSION,
            input_summary=input_summary,
            output_judgment=output_judgment,
            confidence=0.0,
            reasons=[reason],
            fallback_status=fallback_status,
            used_in_final_ranking=False,
        )


def _candidate_input_summary(
    candidate: CandidateUrl,
    *,
    query: str,
    active_rerank: bool = False,
) -> dict[str, Any]:
    metadata = candidate.metadata_json or {}
    intent = source_intent_metadata(
        canonical_url=candidate.canonical_url,
        domain=candidate.domain,
        title=candidate.title,
        query=query,
        known_path_candidate=bool(metadata.get("known_path_candidate")),
    )
    return {
        "query": query,
        "canonical_url": candidate.canonical_url,
        "domain": candidate.domain,
        "title": candidate.title,
        "rank": candidate.rank,
        "snippet": metadata.get("snippet"),
        "deterministic_source_intent": intent,
        "safety_constraints": {
            "cannot_override_ssrf_or_mime_policy": True,
            "cannot_mark_official_without_deterministic_ownership": True,
            "active_rerank_enabled": active_rerank,
        },
    }


def _parse_source_judge_output(text: str) -> dict[str, Any]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("source judge output was not a JSON object")
    label = payload.get("label")
    if label not in SOURCE_JUDGE_ALLOWED_LABELS:
        raise ValueError("source judge label was invalid")
    confidence = payload.get("confidence")
    if not isinstance(confidence, int | float):
        raise ValueError("source judge confidence was missing")
    reasons = payload.get("reasons")
    if isinstance(reasons, str):
        reasons = [reasons]
    if not isinstance(reasons, list):
        reasons = []
    # Ensure all items are strings and truncate if necessary
    reasons = [str(item) for item in reasons if item is not None]
    if not reasons:
        reasons = ["No specific reasons provided by judge."]
    
    adjustment = payload.get("priority_adjustment", 0.0)
    if not isinstance(adjustment, int | float):
        raise ValueError("source judge priority_adjustment must be numeric")
    source_type = payload.get("source_type", "unknown")
    if not isinstance(source_type, str) or not source_type.strip():
        source_type = "unknown"
    triage_decision = payload.get("triage_decision")
    if not isinstance(triage_decision, str) or triage_decision not in SOURCE_TRIAGE_DECISIONS:
        triage_decision = _default_triage_decision(label)
    fetch_priority = payload.get("fetch_priority")
    if not isinstance(fetch_priority, int):
        fetch_priority = _default_fetch_priority(triage_decision, adjustment)
    return {
        "label": label,
        "confidence": round(max(0.0, min(1.0, float(confidence))), 4),
        "reasons": [item[:240] for item in reasons[:5]],
        "priority_adjustment": round(max(-20.0, min(20.0, float(adjustment))), 4),
        "source_type": source_type.strip()[:80],
        "topic_fit": _bounded_choice(
            payload.get("topic_fit"),
            {"high", "medium", "low"},
            "unknown",
        ),
        "authority": _bounded_choice(
            payload.get("authority"),
            {"high", "medium", "low"},
            "unknown",
        ),
        "novelty": _bounded_choice(payload.get("novelty"), {"high", "medium", "low"}, "unknown"),
        "expected_covered_slots": _string_list(payload.get("expected_covered_slots"), limit=8),
        "source_role": _string_value(payload.get("source_role"), default="unknown", limit=80),
        "triage_decision": triage_decision,
        "fetch_priority": max(1, min(99, int(fetch_priority))),
        "risk_flags": _string_list(payload.get("risk_flags"), limit=8),
    }


def _judgment_can_affect_ranking(judgment: dict[str, Any]) -> bool:
    label = str(judgment.get("label") or "")
    confidence = judgment.get("confidence")
    confidence_value = float(confidence) if isinstance(confidence, int | float) else 0.0
    adjustment = judgment.get("priority_adjustment")
    adjustment_value = float(adjustment) if isinstance(adjustment, int | float) else 0.0
    labels_with_rank_effect = {
        "accept",
        "authoritative",
        "relevant",
        "downrank",
        "reject",
        "low_quality",
        "stale",
        "marketing",
        "duplicate",
        "unsafe",
    }
    triage_decision = str(judgment.get("triage_decision") or "")
    if triage_decision in {
        "must_fetch",
        "skip_duplicate",
        "skip_low_value",
        "skip_unsafe_or_invalid",
    }:
        return confidence_value >= 0.45
    if abs(adjustment_value) >= 0.5:
        return confidence_value >= 0.35
    return label in labels_with_rank_effect and confidence_value >= 0.35


def _default_triage_decision(label: str) -> str:
    if label in {"authoritative", "accept"}:
        return "must_fetch"
    if label == "relevant":
        return "fetch_if_budget_allows"
    if label == "duplicate":
        return "skip_duplicate"
    if label == "unsafe":
        return "skip_unsafe_or_invalid"
    if label in {"reject", "low_quality", "marketing", "stale"}:
        return "skip_low_value"
    if label == "downrank":
        return "defer"
    return "defer"


def _default_fetch_priority(triage_decision: str, adjustment: object) -> int:
    if triage_decision == "must_fetch":
        return 1
    if triage_decision == "fetch_if_budget_allows":
        return 20
    if triage_decision == "defer":
        return 60
    if triage_decision.startswith("skip_"):
        return 99
    if isinstance(adjustment, int | float):
        return max(1, min(99, int(round(50 + float(adjustment)))))
    return 50


def _bounded_choice(value: object, allowed: set[str], default: str) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in allowed:
            return normalized
    return default


def _string_value(value: object, *, default: str, limit: int) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()[:limit]
    return default


def _string_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip()[:120] for item in value[:limit] if isinstance(item, str) and item.strip()]


_SOURCE_JUDGE_SYSTEM_PROMPT = (
    "You are an advisory source-quality judge for an evidence-first OSINT pipeline. "
    "Return only JSON with keys label, confidence, reasons, priority_adjustment, source_type, "
    "topic_fit, authority, novelty, expected_covered_slots, source_role, triage_decision, "
    "fetch_priority, and risk_flags. "
    "Use label accept, downrank, reject, or uncertain when possible. "
    "Use triage_decision must_fetch, fetch_if_budget_allows, defer, skip_duplicate, "
    "skip_low_value, or skip_unsafe_or_invalid. "
    "Use only the provided URL/title/snippet/deterministic signals. Do not infer facts "
    "about the research answer. Do not mark a source authoritative unless deterministic "
    "ownership signals already support that label. Prefer downrank or reject for social profiles, "
    "job pages, unrelated listings, SEO mirrors, and weak entity matches when better official "
    "sources are likely."
)
