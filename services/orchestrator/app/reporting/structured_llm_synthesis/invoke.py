from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from services.orchestrator.app.llm import LLMRequest, LLMProvider
from services.orchestrator.app.reporting.markdown import ReportClaimItem, ReportSourceItem
from services.orchestrator.app.query_intent_signals import (
    detect_report_archetype,
    extract_comparison_entities,
)


def _parse_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    data = json.loads(stripped)
    if not isinstance(data, dict):
        raise ValueError("LLM structured synthesis response was not a JSON object")
    return data


def build_structured_synthesis_user_bundle(
    *,
    task_id: UUID,
    research_question: str,
    report_archetype: str,
    plan_intent: str | None,
    research_plan: dict[str, Any] | None,
    claims: list[ReportClaimItem],
    sources: list[ReportSourceItem],
    max_chars: int,
) -> dict[str, Any]:
    evidence_rows: list[dict[str, Any]] = []
    for claim in claims:
        if claim.verification_status != "supported":
            continue
        for ev in claim.support_evidence[:4]:
            evidence_rows.append(
                {
                    "claim_id": str(claim.claim_id),
                    "claim_statement": (claim.statement or "")[:400],
                    "claim_category": claim.claim_category,
                    "claim_evidence_id": str(ev.claim_evidence_id),
                    "canonical_url": ev.canonical_url,
                    "domain": ev.domain,
                    "source_intent": ev.source_intent,
                    "excerpt": (ev.excerpt or "")[:240],
                }
            )
    payload: dict[str, Any] = {
        "task_id": str(task_id),
        "research_question": research_question,
        "deterministic_report_archetype": report_archetype,
        "plan_intent": plan_intent,
        "deterministic_signals": {
            "detect_report_archetype": detect_report_archetype(
                research_question,
                plan_intent=plan_intent,
                source_domains=[s.domain for s in sources if s.domain],
            ),
            "comparison_entities": extract_comparison_entities(research_question),
        },
        "research_plan_summary": {
            "intent": (research_plan or {}).get("intent") if isinstance(research_plan, dict) else None,
            "subquestions": (research_plan or {}).get("subquestions")[:12]
            if isinstance(research_plan, dict)
            else [],
        },
        "sources": [
            {
                "source_document_id": str(s.source_document_id),
                "domain": s.domain,
                "canonical_url": s.canonical_url,
                "title": s.title,
                "source_intent": s.source_intent,
            }
            for s in sources[:40]
        ],
        "evidence_rows": evidence_rows,
    }
    raw = json.dumps(payload, ensure_ascii=False)
    while len(raw) > max_chars and len(payload["evidence_rows"]) > 4:
        payload["evidence_rows"] = payload["evidence_rows"][:-20]
        raw = json.dumps(payload, ensure_ascii=False)
    if len(raw) > max_chars:
        payload["evidence_rows"] = payload["evidence_rows"][:4]
    return payload


_STRUCTURED_SYNTHESIS_SYSTEM_PROMPT = """You are a structured research-report assistant.
Return ONE JSON object only (no markdown fences, no commentary).

Schema (all keys optional unless noted):
{
  "archetype_judge": {
    "report_archetype": "research_survey|technical_comparison|news_update|general",
    "confidence": number 0..1,
    "reason": string,
    "section_outline": [{"title": string, "purpose": string, "required_evidence_types": [string]}],
    "risks": [string]
  },
  "method_cards": [ {
    "method_name": {"text": string, "evidence_ids": [string]},
    "paper_title": {"text": string, "evidence_ids": [string]},
    "problem": {"text": string, "evidence_ids": [string]},
    "motivation": {"text": string, "evidence_ids": [string]},
    "core_method": {"text": string, "evidence_ids": [string]},
    "architecture_or_algorithm": {"text": string, "evidence_ids": [string]},
    "objective_or_loss": {"text": string, "evidence_ids": [string]},
    "datasets_or_tasks": {"text": string, "evidence_ids": [string]},
    "metrics_or_results": {"text": string, "evidence_ids": [string]},
    "limitations": {"text": string, "evidence_ids": [string]},
    "insight": {"text": string, "evidence_ids": [string], "inference_strength": "low|moderate|high", "caveat": string}
  } ],
  "comparison_table": {
    "entities": [string],
    "dimensions": [
      {
        "name": string,
        "why_relevant": string,
        "cells": { "<entity>": {"text": string, "evidence_ids": [string], "competitive_framing": boolean } }
      }
    ]
  },
  "insights": { "insights": [ {"text": string, "type": "synthesis|inference|caveated_projection", "evidence_ids": [string], "caveat": string } ] }
}

Hard rules:
- Every factual text MUST reuse claim_evidence_id values from the input bundle only.
- If you lack evidence for a field, set text to "当前证据不足" and evidence_ids to [].
- Never invent titles, years, authors, metrics, datasets, or URLs not present in excerpts/statements.
- Insights that are not direct excerpts must set type to inference or caveated_projection and include a caveat.
- Prefer the deterministic_report_archetype unless you have strong contradictory evidence in-bundle.
"""


def invoke_structured_synthesis_bundle(
    *,
    llm_provider: LLMProvider,
    llm_model: str,
    max_output_tokens: int,
    task_id: UUID,
    research_question: str,
    report_archetype: str,
    plan_intent: str | None,
    research_plan: dict[str, Any] | None,
    claims: list[ReportClaimItem],
    sources: list[ReportSourceItem],
    max_input_chars: int,
) -> dict[str, Any]:
    user_payload = build_structured_synthesis_user_bundle(
        task_id=task_id,
        research_question=research_question,
        report_archetype=report_archetype,
        plan_intent=plan_intent,
        research_plan=research_plan,
        claims=claims,
        sources=sources,
        max_chars=max_input_chars,
    )
    response = llm_provider.generate(
        LLMRequest(
            system_prompt=_STRUCTURED_SYNTHESIS_SYSTEM_PROMPT,
            user_prompt=json.dumps(user_payload, ensure_ascii=False, sort_keys=True),
            model=llm_model,
            max_output_tokens=max_output_tokens,
            temperature=0.0,
            metadata={
                "task_id": str(task_id),
                "purpose": "structured_llm_synthesis",
                "report_archetype": report_archetype,
            },
        )
    )
    return _parse_json_object(response.text)
