from __future__ import annotations

from uuid import uuid4

from services.orchestrator.app.reporting import (
    ReportClaimItem,
    ReportEvidenceItem,
    ReportSourceItem,
    render_markdown_report,
)


def test_render_markdown_report_contains_required_sections_and_status_labels() -> None:
    source_document_id = uuid4()
    source_chunk_id = uuid4()
    citation_span_id = uuid4()
    claim_id = uuid4()
    support_evidence = ReportEvidenceItem(
        claim_evidence_id=uuid4(),
        citation_span_id=citation_span_id,
        source_document_id=source_document_id,
        source_chunk_id=source_chunk_id,
        relation_type="support",
        score=0.91,
        canonical_url="https://example.com/source",
        domain="example.com",
        chunk_no=0,
        start_offset=10,
        end_offset=42,
        excerpt="Supported evidence excerpt.",
    )
    mixed_claim = ReportClaimItem(
        claim_id=claim_id,
        statement="A mixed conclusion remains under dispute.",
        claim_type="fact",
        confidence=0.74,
        verification_status="mixed",
        rationale="Found 1 support evidence and 1 contradict evidence.",
        support_evidence=[support_evidence],
        contradict_evidence=[
            ReportEvidenceItem(
                claim_evidence_id=uuid4(),
                citation_span_id=uuid4(),
                source_document_id=source_document_id,
                source_chunk_id=uuid4(),
                relation_type="contradict",
                score=0.82,
                canonical_url="https://example.com/source",
                domain="example.com",
                chunk_no=1,
                start_offset=43,
                end_offset=82,
                excerpt="Contradict evidence excerpt.",
            )
        ],
    )

    report = render_markdown_report(
        task_id=uuid4(),
        research_question="What is currently known?",
        revision_no=2,
        claims=[mixed_claim],
        sources=[
            ReportSourceItem(
                source_document_id=source_document_id,
                canonical_url="https://example.com/source",
                domain="example.com",
                title="Example source",
            )
        ],
    )

    assert "# Research Report: What is currently known?" in report.markdown
    assert "## Research Question" in report.markdown
    assert "## Executive Summary" in report.markdown
    assert "## Answer" in report.markdown
    assert "### " in report.markdown
    assert "## Answer Slot Coverage" in report.markdown
    assert "## Evidence Table" in report.markdown
    assert "## Source Scope and Limitations" in report.markdown
    assert "## Unresolved / Low Coverage Areas" in report.markdown
    assert "## Appendix: Claim Evidence Mapping" not in report.markdown
    assert str(claim_id) not in report.markdown
    assert str(citation_span_id) not in report.markdown
    assert "A mixed conclusion remains under dispute." in report.markdown
    assert report.supported_count == 0
    assert report.mixed_count == 1
    assert report.unsupported_count == 0
    assert report.contradicted_count == 0
    assert report.draft_count == 0


def test_render_markdown_report_can_include_debug_mapping_when_enabled() -> None:
    source_document_id = uuid4()
    source_chunk_id = uuid4()
    citation_span_id = uuid4()
    claim_id = uuid4()
    support_evidence = ReportEvidenceItem(
        claim_evidence_id=uuid4(),
        citation_span_id=citation_span_id,
        source_document_id=source_document_id,
        source_chunk_id=source_chunk_id,
        relation_type="support",
        score=0.91,
        canonical_url="https://example.com/source",
        domain="example.com",
        chunk_no=0,
        start_offset=10,
        end_offset=42,
        excerpt="Supported evidence excerpt.",
    )
    claim = ReportClaimItem(
        claim_id=claim_id,
        statement="LangGraph is a framework for stateful agent workflows.",
        claim_type="fact",
        confidence=0.74,
        verification_status="supported",
        rationale="Found support evidence.",
        support_evidence=[support_evidence],
        contradict_evidence=[],
        claim_category="definition",
    )

    report = render_markdown_report(
        task_id=uuid4(),
        research_question="What is LangGraph and how does it work?",
        revision_no=2,
        claims=[claim],
        sources=[
            ReportSourceItem(
                source_document_id=source_document_id,
                canonical_url="https://example.com/source",
                domain="example.com",
                title="Example source",
            )
        ],
        include_ledger_debug_appendix=True,
    )

    assert "## Appendix: Claim Evidence Mapping" in report.markdown
    assert str(claim_id) in report.markdown
    assert str(citation_span_id) in report.markdown
