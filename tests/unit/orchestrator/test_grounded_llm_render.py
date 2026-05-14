from __future__ import annotations

import re
from uuid import UUID, uuid4

from services.orchestrator.app.reporting.grounded_llm import (
    _evidence_anchor_sort_key,
    _render_validated_llm_payload,
)
from services.orchestrator.app.reporting.markdown import ReportClaimItem, ReportEvidenceItem


def _evidence(
    *,
    claim_evidence_id: UUID,
    citation_span_id: UUID,
    source_document_id: UUID,
    source_chunk_id: UUID,
) -> ReportEvidenceItem:
    return ReportEvidenceItem(
        claim_evidence_id=claim_evidence_id,
        citation_span_id=citation_span_id,
        source_document_id=source_document_id,
        source_chunk_id=source_chunk_id,
        relation_type="support",
        score=0.9,
        canonical_url="https://example.com/page",
        domain="example.com",
        chunk_no=1,
        start_offset=0,
        end_offset=12,
        excerpt="sample excerpt text",
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
    assert "## Evidence footnotes" in rendered.markdown
    assert "## 证据来源" not in rendered.markdown
    assert "## 证据脚注" not in rendered.markdown


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
    assert "[Inference]" in rendered.markdown
    assert sidecar["grounded_report_support_type_counts"].get("inference") == 1
    assert "source_scope_mixed" not in rendered.markdown  # label key, not prose
    assert "restricted to direct_evidence" in rendered.markdown


def test_coverage_notes_without_question_alignment() -> None:
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
    assert "## Question Alignment and Coverage" in rendered.markdown
    assert "Coverage notes" in rendered.markdown
    assert "Slot A is weakly covered." in rendered.markdown


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
    )
    ev2 = _evidence(
        claim_evidence_id=eid2,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
    )
    ev3 = _evidence(
        claim_evidence_id=eid3,
        citation_span_id=span,
        source_document_id=sdoc,
        source_chunk_id=schunk,
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
    foot_start = md.index("## Evidence footnotes")
    foot_block = md[foot_start:]
    pos_e2 = foot_block.index("[^e2]:")
    pos_e3 = foot_block.index("[^e3]:")
    assert pos_e2 < pos_e3
    assert "\n\n[^e2]:" in foot_block and "\n\n[^e3]:" in foot_block
    inline = md.split("## Evidence footnotes", 1)[0]
    assert inline.index("[^e1]") < inline.index("[^e2]") < inline.index("[^e3]")
    assert "<span" not in md.lower()
    assert "` · `" not in md
    for line in foot_block.splitlines():
        if line.startswith("[^"):
            assert re.search(r"Trace key: `[0-9a-f-]{36}`", line), line
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
    assert "## 证据来源" in md
    assert "## Evidence footnotes" not in md
    assert "Footnotes" not in md
    assert "claim_evidence" not in md.lower()
    for line in md.split("## 证据来源", 1)[1].splitlines():
        if line.startswith("[^") and "追溯键" in line:
            assert re.search(r"追溯键: `[0-9a-f-]{36}`", line), line
            assert "claim_evidence" not in line


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
