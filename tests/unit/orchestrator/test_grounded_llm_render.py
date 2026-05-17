from __future__ import annotations

import re
from uuid import UUID, uuid4

from services.orchestrator.app.reporting.grounded_llm import (
    _build_grounding_bundle,
    _evidence_anchor_sort_key,
    _filter_appendix_excerpts,
    _render_validated_llm_payload,
    _research_question_requests_recency,
)
from services.orchestrator.app.reporting.markdown import (
    ReportClaimItem,
    ReportEvidenceItem,
    ReportSourceItem,
)


def _evidence(
    *,
    claim_evidence_id: UUID,
    citation_span_id: UUID,
    source_document_id: UUID,
    source_chunk_id: UUID,
    canonical_url: str = "https://example.com/page",
    source_intent: str | None = None,
) -> ReportEvidenceItem:
    return ReportEvidenceItem(
        claim_evidence_id=claim_evidence_id,
        citation_span_id=citation_span_id,
        source_document_id=source_document_id,
        source_chunk_id=source_chunk_id,
        relation_type="support",
        score=0.9,
        canonical_url=canonical_url,
        domain=canonical_url.split("//", 1)[-1].split("/", 1)[0],
        chunk_no=1,
        start_offset=0,
        end_offset=12,
        excerpt="sample excerpt text",
        source_intent=source_intent,
    )


def _supported_claim(
    *,
    claim_id: UUID,
    evidence: ReportEvidenceItem,
) -> ReportClaimItem:
    return ReportClaimItem(
        claim_id=claim_id,
        statement="Supported statement.",
        claim_type="factual",
        confidence=0.9,
        verification_status="supported",
        rationale=None,
        support_evidence=[evidence],
        contradict_evidence=[],
        support_level="normal",
    )


def _mixed_claim(
    *,
    claim_id: UUID,
    evidence: ReportEvidenceItem,
) -> ReportClaimItem:
    return ReportClaimItem(
        claim_id=claim_id,
        statement="Mixed statement.",
        claim_type="factual",
        confidence=0.5,
        verification_status="mixed",
        rationale=None,
        support_evidence=[evidence],
        contradict_evidence=[],
        support_level="weak",
    )


def test_render_drops_non_direct_executive_items() -> None:
    task_id = uuid4()
    cid = uuid4()
    eid = uuid4()
    span = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    ev = _evidence(
        claim_evidence_id=eid,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
    )
    claim = _supported_claim(claim_id=cid, evidence=ev)
    payload = {
        "title": "Test title",
        "executive_summary": [
            {
                "text": "Inference bullet",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid)],
                "citation_span_ids": [str(span)],
                "support_type": "inference",
            },
            {
                "text": "Direct bullet",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid)],
                "citation_span_ids": [str(span)],
                "support_type": "direct_evidence",
            },
        ],
        "sections": [],
        "uncertainties": [],
    }
    rendered, sidecar = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question="RQ",
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="en-US",
        answer_relevant_claim_count=1,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=False,
    )
    assert "Direct bullet" in rendered.markdown
    assert "Inference bullet" not in rendered.markdown
    assert sidecar["grounded_report_support_type_counts"]["direct_evidence"] == 1
    assert "[^e1]" in rendered.markdown
    assert "## Appendix: Sources and evidence" in rendered.markdown
    assert "## 证据来源" not in rendered.markdown
    assert "## 证据脚注" not in rendered.markdown
    body = rendered.markdown.split("## Appendix: Sources and evidence", 1)[0].lower()
    for forbidden in (
        "grounded llm report writer",
        "revision",
        "claim_id",
        "claim_evidence",
        "citation_span",
        "[direct evidence]",
        "[inference]",
        "overview槽位",
        "planner子问题",
    ):
        assert forbidden not in body


def test_render_keeps_inference_in_uncertainties() -> None:
    task_id = uuid4()
    cid = uuid4()
    eid = uuid4()
    span = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    ev = _evidence(
        claim_evidence_id=eid,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
    )
    claim = _mixed_claim(claim_id=cid, evidence=ev)
    payload = {
        "title": "T",
        "executive_summary": [],
        "sections": [],
        "uncertainties": [
            {
                "text": "Uncertain inference",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid)],
                "citation_span_ids": [str(span)],
                "support_type": "inference",
            }
        ],
    }
    rendered, sidecar = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question="RQ",
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="en-US",
        answer_relevant_claim_count=1,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=False,
    )
    assert "Uncertain inference" in rendered.markdown
    assert "[Inference]" not in rendered.markdown
    assert "direct_evidence" not in rendered.markdown.lower()
    assert sidecar["grounded_report_support_type_counts"].get("inference") == 1
    assert "## V. Insufficient evidence and open questions" in rendered.markdown


def test_coverage_notes_only_in_debug_appendix() -> None:
    task_id = uuid4()
    cid = uuid4()
    eid = uuid4()
    span = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    ev = _evidence(
        claim_evidence_id=eid,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
    )
    claim = _supported_claim(claim_id=cid, evidence=ev)
    payload = {
        "title": "T",
        "coverage_notes": ["Slot A is weakly covered."],
        "executive_summary": [
            {
                "text": "Direct only",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid)],
                "citation_span_ids": [str(span)],
            }
        ],
        "sections": [],
        "uncertainties": [],
    }
    rendered, _ = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question="RQ",
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="en-US",
        answer_relevant_claim_count=1,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=False,
    )
    assert "Slot A is weakly covered." not in rendered.markdown
    rendered_dbg, _ = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question="RQ",
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="en-US",
        answer_relevant_claim_count=1,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=True,
    )
    assert "Slot A is weakly covered." in rendered_dbg.markdown
    assert "## Appendix: Orchestration trace (debug)" in rendered_dbg.markdown


def test_research_question_requests_recency() -> None:
    assert _research_question_requests_recency("请总结近30天内的官方更新") is True
    assert _research_question_requests_recency("last 14 days of releases") is True
    assert _research_question_requests_recency("静态架构说明") is False


def test_grounding_bundle_marks_recency_flag() -> None:
    bundle = _build_grounding_bundle(
        task_id=uuid4(),
        research_question="近30天有哪些安全公告？",
        revision_no=1,
        claims=[],
        sources=[],
        report_language="zh-CN",
    )
    assert bundle["temporal_constraints"]["recency_detected"] is True
    assert any("recency window" in str(rule) for rule in bundle["rules"])


def test_grounding_bundle_includes_method_survey_cards_for_research_survey() -> None:
    cid = uuid4()
    eid = uuid4()
    span = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    ev = _evidence(
        claim_evidence_id=eid,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
        canonical_url="https://arxiv.org/abs/9999.9999",
    )
    claim = _supported_claim(claim_id=cid, evidence=ev)
    bundle = _build_grounding_bundle(
        task_id=uuid4(),
        research_question="literature review on test topic",
        revision_no=1,
        claims=[claim],
        sources=[
            ReportSourceItem(
                source_document_id=sdoc,
                canonical_url=ev.canonical_url,
                domain=ev.domain,
                title="Example paper title",
            )
        ],
        report_language="en-US",
        report_archetype="research_survey",
    )
    cards = bundle.get("method_survey_cards")
    assert isinstance(cards, list)
    assert len(cards) == 1
    assert cards[0]["display_name"] == "Example paper title"


def test_appendix_filters_bio_boilerplate() -> None:
    assert _filter_appendix_excerpts(
        ["Alice is a product marketing manager at ExampleCo.", "Real changelog: fixed bug X."]
    ) == ["Real changelog: fixed bug X."]


def test_fallback_summary_when_executive_empty() -> None:
    task_id = uuid4()
    cid = uuid4()
    eid = uuid4()
    span = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    ev = _evidence(
        claim_evidence_id=eid,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
    )
    claim = _supported_claim(claim_id=cid, evidence=ev)
    payload = {
        "title": "T",
        "executive_summary": [],
        "sections": [
            {
                "heading": "一、核心发现/更新主线",
                "items": [
                    {
                        "text": "Section finding one.",
                        "claim_ids": [str(cid)],
                        "claim_evidence_ids": [str(eid)],
                        "citation_span_ids": [str(span)],
                    }
                ],
            }
        ],
        "uncertainties": [],
    }
    rendered, _ = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question="RQ",
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="zh-CN",
        answer_relevant_claim_count=1,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=False,
    )
    body = rendered.markdown.split("## 附录：来源与证据", 1)[0]
    assert "缺少可核验的摘要段落" not in body
    assert "Section finding one." in body
    assert "模型生成" not in body
    summary_block = body.split("## 摘要", 1)[1].split("##", 1)[0]
    assert "Section finding one." in summary_block


def test_hides_summary_when_executive_and_sections_empty() -> None:
    task_id = uuid4()
    cid = uuid4()
    eid = uuid4()
    span = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    ev = _evidence(
        claim_evidence_id=eid,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
    )
    claim = _mixed_claim(claim_id=cid, evidence=ev)
    payload = {
        "title": "T",
        "executive_summary": [],
        "sections": [],
        "uncertainties": [
            {
                "text": "现有证据只能支持谨慎表述。",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid)],
                "citation_span_ids": [str(span)],
                "support_type": "inference",
            }
        ],
    }
    rendered, _ = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question="RQ",
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="zh-CN",
        answer_relevant_claim_count=1,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=False,
    )

    body = rendered.markdown.split("## 附录：来源与证据", 1)[0]
    assert "## 摘要" not in body
    assert "缺少可核验的摘要段落" not in body
    assert "本报告未生成独立摘要段落" not in body
    assert "现有证据只能支持谨慎表述。" in body


def test_evidence_anchor_sort_key_orders_numeric_suffix() -> None:
    assert _evidence_anchor_sort_key("e2") < _evidence_anchor_sort_key("e10")
    assert _evidence_anchor_sort_key("e1") < _evidence_anchor_sort_key("e2")


def test_evidence_footnotes_sorted_numerically_and_separate_paragraphs() -> None:
    task_id = uuid4()
    span = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    cid = uuid4()
    eid1, eid2, eid3 = uuid4(), uuid4(), uuid4()
    ev1 = _evidence(
        claim_evidence_id=eid1,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
        canonical_url="https://example.com/a",
    )
    ev2 = _evidence(
        claim_evidence_id=eid2,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
        canonical_url="https://example.com/b",
    )
    ev3 = _evidence(
        claim_evidence_id=eid3,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
        canonical_url="https://example.com/c",
    )
    claim = ReportClaimItem(
        claim_id=cid,
        statement="Multi-evidence statement.",
        claim_type="factual",
        confidence=0.9,
        verification_status="supported",
        rationale=None,
        support_evidence=[ev1, ev2, ev3],
        contradict_evidence=[],
        support_level="normal",
    )
    payload = {
        "title": "T",
        "executive_summary": [
            {
                "text": "Uses multiple anchors",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid3), str(eid1), str(eid2)],
                "citation_span_ids": [str(span)],
            }
        ],
        "sections": [],
        "uncertainties": [],
    }
    rendered, _ = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question="RQ",
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="en-US",
        answer_relevant_claim_count=1,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=False,
    )
    md = rendered.markdown
    foot_start = md.index("## Appendix: Sources and evidence")
    foot_block = md[foot_start:]
    pos_e2 = foot_block.index("[^e2]:")
    pos_e3 = foot_block.index("[^e3]:")
    assert pos_e2 < pos_e3
    assert "\n\n[^e2]:" in foot_block and "\n\n[^e3]:" in foot_block
    inline = md.split("## Appendix: Sources and evidence", 1)[0]
    assert inline.index("[^e1]") < inline.index("[^e2]") < inline.index("[^e3]")
    assert "<span" not in md.lower()
    assert "` · `" not in md
    for line in foot_block.splitlines():
        if line.startswith("[^") and "]: " in line:
            assert "Internal trace ids" not in line
    assert "claim_evidence" not in md.lower()


def test_chinese_grounded_report_footnote_heading_and_trace_label() -> None:
    task_id = uuid4()
    cid = uuid4()
    eid = uuid4()
    span = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    ev = _evidence(
        claim_evidence_id=eid,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
    )
    claim = _supported_claim(claim_id=cid, evidence=ev)
    payload = {
        "title": "中文标题测",
        "executive_summary": [
            {
                "text": "直接要点测",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid)],
                "citation_span_ids": [str(span)],
                "support_type": "direct_evidence",
            }
        ],
        "sections": [],
        "uncertainties": [],
    }
    rendered, _ = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question="研究问题测",
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="zh-CN",
        answer_relevant_claim_count=1,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=False,
    )
    md = rendered.markdown
    assert "## 附录：来源与证据" in md
    assert "## Evidence footnotes" not in md
    assert "Footnotes" not in md
    assert "claim_evidence" not in md.lower()
    appendix = md.split("## 附录：来源与证据", 1)[1]
    assert "https://example.com/page" in appendix
    assert "Internal trace ids" not in appendix
    for line in appendix.splitlines():
        if line.startswith("[^") and "]: " in line:
            assert not re.search(r"`[0-9a-f-]{36}`", line), line


def test_competitive_flag_drops_executive_item() -> None:
    task_id = uuid4()
    cid = uuid4()
    eid = uuid4()
    span = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    ev = _evidence(
        claim_evidence_id=eid,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
    )
    claim = _supported_claim(claim_id=cid, evidence=ev)
    payload = {
        "title": "T",
        "executive_summary": [
            {
                "text": "Too strong competitive",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid)],
                "citation_span_ids": [str(span)],
                "support_type": "direct_evidence",
                "competitive_implication": True,
            },
            {
                "text": "Safe",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid)],
                "citation_span_ids": [str(span)],
            },
        ],
        "sections": [],
        "uncertainties": [],
    }
    rendered, _ = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question="RQ",
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="en-US",
        answer_relevant_claim_count=1,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=False,
    )
    assert "Safe" in rendered.markdown
    assert "Too strong competitive" not in rendered.markdown


def test_executive_summary_uses_paragraph_breaks() -> None:
    task_id = uuid4()
    cid = uuid4()
    eid = uuid4()
    span = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    ev = _evidence(
        claim_evidence_id=eid,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
    )
    claim = _supported_claim(claim_id=cid, evidence=ev)
    payload = {
        "title": "T",
        "executive_summary": [
            {
                "text": "First paragraph for trend.",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid)],
                "citation_span_ids": [str(span)],
            },
            {
                "text": "Second paragraph for implications.",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid)],
                "citation_span_ids": [str(span)],
            },
        ],
        "sections": [],
        "uncertainties": [],
    }
    rendered, _ = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question="RQ",
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="en-US",
        answer_relevant_claim_count=1,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=False,
    )
    summary_block = rendered.markdown.split("## Executive summary", 1)[1].split("##", 1)[0]
    assert "\n\n" in summary_block.strip()
    assert "First paragraph for trend." in summary_block
    assert "Second paragraph for implications." in summary_block


def test_same_canonical_url_reuses_single_footnote_anchor() -> None:
    task_id = uuid4()
    span = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    cid = uuid4()
    eid1, eid2 = uuid4(), uuid4()
    url = "https://docs.example.com/changelog"
    ev1 = _evidence(
        claim_evidence_id=eid1,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
        canonical_url=url,
    )
    ev2 = _evidence(
        claim_evidence_id=eid2,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
        canonical_url=url,
    )
    claim = ReportClaimItem(
        claim_id=cid,
        statement="Same URL twice.",
        claim_type="factual",
        confidence=0.9,
        verification_status="supported",
        rationale=None,
        support_evidence=[ev1, ev2],
        contradict_evidence=[],
        support_level="normal",
    )
    payload = {
        "title": "T",
        "executive_summary": [
            {
                "text": "Both evidences same page",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid1), str(eid2)],
                "citation_span_ids": [str(span)],
            }
        ],
        "sections": [],
        "uncertainties": [],
    }
    rendered, _ = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question="RQ",
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="en-US",
        answer_relevant_claim_count=1,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=False,
    )
    inline = rendered.markdown.split("## Appendix: Sources and evidence", 1)[0]
    assert inline.count("[^e1]") == 1
    assert "[^e2]" not in inline


def test_debug_appendix_includes_internal_ids_and_trace() -> None:
    task_id = uuid4()
    cid = uuid4()
    eid = uuid4()
    span = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    ev = _evidence(
        claim_evidence_id=eid,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
    )
    claim = _supported_claim(claim_id=cid, evidence=ev)
    payload = {
        "title": "T",
        "executive_summary": [
            {
                "text": "Body",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid)],
                "citation_span_ids": [str(span)],
            }
        ],
        "sections": [],
        "uncertainties": [],
    }
    rendered, _ = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question="RQ",
        revision_no=2,
        claims=[claim],
        sources=[],
        report_language="en-US",
        answer_relevant_claim_count=1,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=True,
    )
    md = rendered.markdown
    assert "## Appendix: Orchestration trace (debug)" in md
    assert str(task_id) in md
    assert "internal revision `2`" in md
    appendix = md.split("## Appendix: Sources and evidence", 1)[1].split(
        "## Appendix: Orchestration trace (debug)", 1
    )[0]
    assert re.search(r"`[0-9a-f-]{36}`", appendix), appendix


def test_zh_body_excludes_engineering_tokens() -> None:
    task_id = uuid4()
    cid = uuid4()
    eid = uuid4()
    span = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    ev = _evidence(
        claim_evidence_id=eid,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
    )
    claim = _supported_claim(claim_id=cid, evidence=ev)
    payload = {
        "title": "标题",
        "executive_summary": [
            {
                "text": "摘要段一。",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid)],
                "citation_span_ids": [str(span)],
            },
            {
                "text": "摘要段二。",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid)],
                "citation_span_ids": [str(span)],
            },
        ],
        "sections": [],
        "uncertainties": [],
    }
    rendered, _ = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question="研究问题",
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="zh-CN",
        answer_relevant_claim_count=1,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=False,
    )
    body = rendered.markdown.split("## 附录：来源与证据", 1)[0]
    for token in (
        "grounded LLM report writer",
        "revision",
        "claim_id",
        "claim_evidence",
        "citation_span",
        "[直接证据]",
        "[推断]",
        "Overview槽位",
        "planner子问题",
    ):
        assert token not in body
    summary = body.split("## 摘要", 1)[1]
    assert summary.count("\n\n") >= 1


ZH_TECH_COMPARE_Q = (
    "详细解释 LangGraph 是什么、它如何工作，以及它相比 AutoGen / CrewAI 的主要区别。"
)


def test_grounded_report_renders_comparison_table_with_evidence() -> None:
    task_id = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    cid_lg, cid_ag, cid_cr = uuid4(), uuid4(), uuid4()
    eid_lg, eid_ag, eid_cr = uuid4(), uuid4(), uuid4()
    span_lg, span_ag, span_cr = uuid4(), uuid4(), uuid4()
    ev_lg = _evidence(
        claim_evidence_id=eid_lg,
        citation_span_id=span_lg,
        source_document_id=sdoc,
        source_chunk_id=schunk,
        canonical_url="https://langchain-ai.github.io/langgraph/",
        source_intent="official_docs_reference",
    )
    ev_ag = _evidence(
        claim_evidence_id=eid_ag,
        citation_span_id=span_ag,
        source_document_id=sdoc,
        source_chunk_id=schunk,
        canonical_url="https://microsoft.github.io/autogen/",
        source_intent="official_docs_reference",
    )
    ev_cr = _evidence(
        claim_evidence_id=eid_cr,
        citation_span_id=span_cr,
        source_document_id=sdoc,
        source_chunk_id=schunk,
        canonical_url="https://docs.crewai.com/",
        source_intent="official_docs_reference",
    )
    claim_lg = ReportClaimItem(
        claim_id=cid_lg,
        statement="LangGraph 提供有状态图编排与检查点。",
        claim_type="factual",
        confidence=0.9,
        verification_status="supported",
        rationale=None,
        support_evidence=[ev_lg],
        contradict_evidence=[],
        support_level="normal",
    )
    claim_ag = ReportClaimItem(
        claim_id=cid_ag,
        statement="AutoGen 支持多代理对话与工具调用。",
        claim_type="factual",
        confidence=0.9,
        verification_status="supported",
        rationale=None,
        support_evidence=[ev_ag],
        contradict_evidence=[],
        support_level="normal",
    )
    claim_cr = ReportClaimItem(
        claim_id=cid_cr,
        statement="CrewAI 以角色与任务为中心组织代理协作。",
        claim_type="factual",
        confidence=0.9,
        verification_status="supported",
        rationale=None,
        support_evidence=[ev_cr],
        contradict_evidence=[],
        support_level="normal",
    )
    claims = [claim_lg, claim_ag, claim_cr]
    payload = {
        "title": "研究报告标题",
        "executive_summary": [
            {
                "text": "本摘要概述 LangGraph 的证据要点。",
                "claim_ids": [str(cid_lg)],
                "claim_evidence_ids": [str(eid_lg)],
                "citation_span_ids": [str(span_lg)],
            }
        ],
        "sections": [
            {
                "heading": "LangGraph 如何工作（机制与执行模型）",
                "items": [
                    {
                        "text": "正文段落引用 AutoGen 材料。",
                        "claim_ids": [str(cid_ag)],
                        "claim_evidence_ids": [str(eid_ag)],
                        "citation_span_ids": [str(span_ag)],
                    }
                ],
            }
        ],
        "uncertainties": [],
    }
    rendered, _ = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question=ZH_TECH_COMPARE_Q,
        revision_no=1,
        claims=claims,
        sources=[],
        report_language="zh-CN",
        answer_relevant_claim_count=3,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=False,
        plan_intent=None,
    )
    md = rendered.markdown
    assert "## 结构化技术对比（证据绑定）" in md
    assert "| LangGraph |" in md and "| AutoGen |" in md and "| CrewAI |" in md
    assert "| 核心抽象 |" in md
    assert md.count("|") >= 12
    assert "当前证据不足" in md or "[^e" in md
    assert "[^e1]" in md


def test_footnotes_are_contiguous_after_filtering() -> None:
    task_id = uuid4()
    span = uuid4()
    sdoc = uuid4()
    schunk = uuid4()
    cid = uuid4()
    eid1, eid2, eid3 = uuid4(), uuid4(), uuid4()
    ev1 = _evidence(
        claim_evidence_id=eid1,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
        canonical_url="https://example.com/a",
    )
    ev2 = _evidence(
        claim_evidence_id=eid2,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
        canonical_url="https://example.com/b",
    )
    ev3 = _evidence(
        claim_evidence_id=eid3,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
        canonical_url="https://example.com/c",
    )
    claim = ReportClaimItem(
        claim_id=cid,
        statement="Holds three evidences.",
        claim_type="factual",
        confidence=0.9,
        verification_status="supported",
        rationale=None,
        support_evidence=[ev1, ev2, ev3],
        contradict_evidence=[],
        support_level="normal",
    )
    payload = {
        "title": "T",
        "executive_summary": [
            {
                "text": "Cites first and third only.",
                "claim_ids": [str(cid)],
                "claim_evidence_ids": [str(eid1), str(eid3)],
                "citation_span_ids": [str(span)],
            }
        ],
        "sections": [],
        "uncertainties": [],
    }
    rendered, _ = _render_validated_llm_payload(
        payload,
        task_id=task_id,
        research_question="Static overview",
        revision_no=1,
        claims=[claim],
        sources=[],
        report_language="en-US",
        answer_relevant_claim_count=1,
        excluded_low_quality_claim_count=0,
        include_ledger_debug_appendix=False,
    )
    md = rendered.markdown
    assert "[^e3]:" not in md
    assert re.search(r"^\[\^e1\]:", md, re.M)
    assert re.search(r"^\[\^e2\]:", md, re.M)
