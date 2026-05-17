from __future__ import annotations

import re
from uuid import uuid4

from services.orchestrator.app.reporting.markdown import ReportClaimItem, ReportEvidenceItem, render_markdown_report
from services.orchestrator.app.reporting.structured_llm_synthesis.render import (
    append_to_rendered_markdown,
    render_validated_bundle_markdown,
)
from services.orchestrator.app.reporting.structured_llm_synthesis.schema import (
    ArchetypeJudgePayload,
    ArchetypeSectionOutline,
    EvidenceBackedText,
    MethodCardPayload,
    MethodInsightPayload,
    StructuredSynthesisBundle,
    StructuredSynthesisStageFlags,
)
from services.orchestrator.app.reporting.structured_llm_synthesis.validate import (
    bundle_has_renderable_content,
    validate_and_sanitize_bundle,
)


def _ev(*, eid: object, sid: object, url: str, intent: str | None = None) -> ReportEvidenceItem:
    return ReportEvidenceItem(
        claim_evidence_id=eid,
        citation_span_id=uuid4(),
        source_document_id=sid,
        source_chunk_id=uuid4(),
        relation_type="support",
        score=0.9,
        canonical_url=url,
        domain=url.split("//", 1)[-1].split("/", 1)[0],
        chunk_no=1,
        start_offset=0,
        end_offset=8,
        excerpt="ex",
        source_intent=intent,
    )


def _claim(statement: str, ev: ReportEvidenceItem) -> ReportClaimItem:
    return ReportClaimItem(
        claim_id=uuid4(),
        statement=statement,
        claim_type="factual",
        confidence=0.9,
        verification_status="supported",
        rationale=None,
        support_evidence=[ev],
        contradict_evidence=[],
        claim_category="mechanism",
        support_level="normal",
    )


def test_validate_rejects_unknown_evidence_ids() -> None:
    eid = uuid4()
    sid = uuid4()
    ev = _ev(eid=eid, sid=sid, url="https://arxiv.org/abs/1", intent="official_docs_reference")
    claim = _claim("Method X uses graphs.", ev)
    raw = {
        "archetype_judge": {
            "report_archetype": "research_survey",
            "confidence": 0.9,
            "reason": "ok",
            "section_outline": [],
            "risks": [],
        },
        "method_cards": [
            {
                "method_name": {"text": "Method X", "evidence_ids": [str(eid), "00000000-0000-0000-0000-000000000099"]},
            }
        ],
    }
    bundle, warns = validate_and_sanitize_bundle(
        raw,
        claims=[claim],
        research_question="survey on topic",
        deterministic_archetype="research_survey",
        confidence_threshold=0.5,
        flags=StructuredSynthesisStageFlags(
            structure=True,
            method_cards=True,
            comparison_table=False,
            insights=False,
        ),
    )
    assert bundle is not None
    assert "dropped_unknown_evidence_ids" in warns
    assert bundle.method_cards[0].method_name.evidence_ids == [str(eid)]


def test_github_topic_evidence_demoted_to_insufficient_for_core_field() -> None:
    eid = uuid4()
    sid = uuid4()
    ev = _ev(eid=eid, sid=sid, url="https://github.com/x/y", intent="github_topic")
    claim = _claim("Some claim.", ev)
    raw = {
        "archetype_judge": {
            "report_archetype": "research_survey",
            "confidence": 0.95,
            "reason": "ok",
            "section_outline": [],
            "risks": [],
        },
        "method_cards": [
            {
                "method_name": {"text": "Alpha", "evidence_ids": [str(eid)]},
            }
        ],
    }
    bundle, warns = validate_and_sanitize_bundle(
        raw,
        claims=[claim],
        research_question="survey",
        deterministic_archetype="research_survey",
        confidence_threshold=0.5,
        flags=StructuredSynthesisStageFlags(
            structure=True,
            method_cards=True,
            comparison_table=False,
            insights=False,
        ),
    )
    assert bundle is not None
    assert "non_core_evidence_for_factual_field" in warns
    assert bundle.method_cards[0].method_name.text == "当前证据不足"


def test_competitive_claim_gets_prefix_in_comparison_cell() -> None:
    eid = uuid4()
    sid = uuid4()
    ev = _ev(eid=eid, sid=sid, url="https://docs.example.com/a", intent="official_docs_reference")
    claim = _claim("LangGraph 明显优于 AutoGen 的延迟表现。", ev)
    raw = {
        "archetype_judge": {
            "report_archetype": "technical_comparison",
            "confidence": 0.9,
            "reason": "ok",
            "section_outline": [],
            "risks": [],
        },
        "comparison_table": {
            "entities": ["LangGraph", "AutoGen"],
            "dimensions": [
                {
                    "name": "延迟",
                    "why_relevant": "性能",
                    "cells": {
                        "LangGraph": {"text": "更低延迟", "evidence_ids": [str(eid)], "competitive_framing": False},
                        "AutoGen": {"text": "当前证据不足", "evidence_ids": [], "competitive_framing": False},
                    },
                }
            ],
        },
    }
    bundle, warns = validate_and_sanitize_bundle(
        raw,
        claims=[claim],
        research_question="LangGraph 和 AutoGen 的区别是什么？各自如何工作？",
        deterministic_archetype="technical_comparison",
        confidence_threshold=0.5,
        flags=StructuredSynthesisStageFlags(
            structure=True,
            method_cards=False,
            comparison_table=True,
            insights=False,
        ),
    )
    assert bundle is not None
    assert "comparison_cell_competitive_tone" in warns
    cell = bundle.comparison_table.dimensions[0].cells["LangGraph"]
    assert cell.text.startswith("【竞争性说法】")


def test_inference_insight_requires_caveat_and_two_evidence_ids() -> None:
    e1, e2 = uuid4(), uuid4()
    sid = uuid4()
    ev1 = _ev(eid=e1, sid=sid, url="https://a.example.com/1", intent="official_docs_reference")
    ev2 = _ev(eid=e2, sid=sid, url="https://a.example.com/2", intent="official_docs_reference")
    c1 = _claim("Fact one.", ev1)
    c2 = _claim("Fact two.", ev2)
    raw = {
        "archetype_judge": {
            "report_archetype": "research_survey",
            "confidence": 0.9,
            "reason": "ok",
            "section_outline": [],
            "risks": [],
        },
        "insights": {
            "insights": [
                {"text": "推断结论", "type": "inference", "evidence_ids": [str(e1)], "caveat": ""},
                {
                    "text": "推断结论二",
                    "type": "inference",
                    "evidence_ids": [str(e1), str(e2)],
                    "caveat": "证据有限",
                },
            ]
        },
    }
    bundle, warns = validate_and_sanitize_bundle(
        raw,
        claims=[c1, c2],
        research_question="survey",
        deterministic_archetype="research_survey",
        confidence_threshold=0.5,
        flags=StructuredSynthesisStageFlags(
            structure=True,
            method_cards=False,
            comparison_table=False,
            insights=True,
        ),
    )
    assert bundle is not None
    assert "insight_inference_short_evidence" in warns
    assert len(bundle.insights.insights) == 1


def test_invalid_archetype_returns_none() -> None:
    raw = {
        "archetype_judge": {
            "report_archetype": "alien_archetype",
            "confidence": 0.99,
            "reason": "bad",
            "section_outline": [],
            "risks": [],
        }
    }
    bundle, warns = validate_and_sanitize_bundle(
        raw,
        claims=[],
        research_question="q",
        deterministic_archetype="general",
        confidence_threshold=0.5,
        flags=StructuredSynthesisStageFlags(structure=True),
    )
    assert bundle is None
    assert any("schema_validation_error" in w for w in warns)


def test_research_survey_golden_fixture_renders_appendix() -> None:
    eid1, eid2 = uuid4(), uuid4()
    sid = uuid4()
    ev1 = _ev(eid=eid1, sid=sid, url="https://arxiv.org/abs/9", intent="official_docs_reference")
    ev2 = _ev(eid=eid2, sid=sid, url="https://arxiv.org/abs/8", intent="official_docs_reference")
    claim = _claim("Survey claim about topic.", ev1)
    claim = ReportClaimItem(
        claim_id=claim.claim_id,
        statement=claim.statement,
        claim_type=claim.claim_type,
        confidence=claim.confidence,
        verification_status=claim.verification_status,
        rationale=claim.rationale,
        support_evidence=[ev1, ev2],
        contradict_evidence=[],
        claim_category=claim.claim_category,
        support_level=claim.support_level,
    )
    raw = StructuredSynthesisBundle(
        archetype_judge=ArchetypeJudgePayload(
            report_archetype="research_survey",
            confidence=0.88,
            reason="Literature-style question.",
            section_outline=[
                ArchetypeSectionOutline(
                    title="脉络", purpose="thread", required_evidence_types=["mechanism"]
                )
            ],
            risks=["coverage"],
        ),
        method_cards=[
            MethodCardPayload(
                method_name=EvidenceBackedText(text="Method Z", evidence_ids=[str(eid1)]),
                core_method=EvidenceBackedText(text="Uses graphs.", evidence_ids=[str(eid1)]),
                insight=MethodInsightPayload(
                    text="可能形成新的评测方向。",
                    evidence_ids=[str(eid1), str(eid2)],
                    inference_strength="moderate",
                    caveat="材料有限，需谨慎外推。",
                ),
            )
        ],
    )
    bundle, _ = validate_and_sanitize_bundle(
        raw.model_dump(mode="json"),
        claims=[claim],
        research_question="文献综述：主题",
        deterministic_archetype="research_survey",
        confidence_threshold=0.5,
        flags=StructuredSynthesisStageFlags(
            structure=True,
            method_cards=True,
            comparison_table=False,
            insights=False,
        ),
    )
    assert bundle is not None
    base = render_markdown_report(
        task_id=uuid4(),
        research_question="文献综述：主题",
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="zh-CN",
        report_archetype="research_survey",
    )
    frag = render_validated_bundle_markdown(
        bundle,
        claims=[claim],
        base_markdown=base.markdown,
        report_language="zh-CN",
        flags=StructuredSynthesisStageFlags(
            structure=True,
            method_cards=True,
            comparison_table=False,
            insights=False,
        ),
    )
    merged = append_to_rendered_markdown(base, fragment=frag)
    assert "LLM 辅助结构化综合" in merged.markdown
    assert "方法/材料卡片" in merged.markdown
    tail = frag.split("**结构化综合脚注**", 1)
    if len(tail) > 1:
        def_nums = [int(m) for m in re.findall(r"\[\^(\d+)\]:", tail[1])]
        assert def_nums == list(range(def_nums[0], def_nums[0] + len(def_nums)))


def test_invalid_payload_schema_returns_none() -> None:
    bundle, warns = validate_and_sanitize_bundle(
        {"archetype_judge": {"confidence": "not-a-float"}},
        claims=[],
        research_question="q",
        deterministic_archetype="general",
        confidence_threshold=0.5,
        flags=StructuredSynthesisStageFlags(structure=True),
    )
    assert bundle is None
    assert any("schema_validation_error" in w for w in warns)


def test_bundle_has_renderable_respects_flags() -> None:
    b = StructuredSynthesisBundle(
        archetype_judge=ArchetypeJudgePayload(
            report_archetype="research_survey",
            confidence=0.9,
            reason="x",
        )
    )
    assert bundle_has_renderable_content(
        b,
        StructuredSynthesisStageFlags(structure=True, method_cards=False),
    )
    assert not bundle_has_renderable_content(
        b,
        StructuredSynthesisStageFlags(structure=False, method_cards=False),
    )
