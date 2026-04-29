from __future__ import annotations

from pathlib import Path

from services.orchestrator.app.research_quality import (
    DROPPED_SOURCE_REASONS,
    EVIDENCE_LINEAGE_FIELDS,
    QUALITY_DIAGNOSTIC_FIELDS,
    EvidenceCandidate,
    SourceYieldSummary,
    build_slot_coverage_summary,
    contribution_level_for_counts,
    evidence_candidate_id,
    normalize_dropped_reasons,
    summarize_evidence_yield,
)


def test_evidence_candidate_serializes_round_trip() -> None:
    candidate_id = evidence_candidate_id(
        source_chunk_id="chunk-1",
        start_offset=10,
        end_offset=42,
        excerpt="OpenSearch is a distributed search and analytics engine.",
    )
    candidate = EvidenceCandidate(
        evidence_candidate_id=candidate_id,
        source_document_id="source-1",
        source_chunk_id="chunk-1",
        citation_span_id=None,
        slot_ids=("definition",),
        source_intent="official_about",
        excerpt="OpenSearch is a distributed search and analytics engine.",
        start_offset=10,
        end_offset=42,
        salience_score=0.81,
        quality_score=0.77,
        extraction_strategy="paragraph_window_v1",
        rejection_reasons=(),
        metadata={"claim_category": "definition"},
    )

    payload = candidate.to_payload()
    restored = EvidenceCandidate.from_payload(payload)

    assert restored == candidate
    assert payload["evidence_candidate_id"] == candidate_id


def test_evidence_yield_and_slot_coverage_summaries_are_structured() -> None:
    candidates = [
        {
            "evidence_candidate_id": "ec_1",
            "source_document_id": "source-1",
            "slot_ids": ["definition"],
            "rejection_reasons": [],
        },
        {
            "evidence_candidate_id": "ec_2",
            "source_document_id": "source-1",
            "slot_ids": ["mechanism"],
            "rejection_reasons": [],
        },
        {
            "evidence_candidate_id": "ec_3",
            "source_document_id": "source-1",
            "slot_ids": ["privacy"],
            "rejection_reasons": ["off_intent"],
        },
    ]

    evidence_summary = summarize_evidence_yield(
        candidates,
        accepted_candidate_ids={"ec_1"},
        query="What is OpenSearch and how does it work?",
    )
    slot_summary = build_slot_coverage_summary(
        "What is OpenSearch and how does it work?",
        evidence_candidates=candidates,
        claim_rows=[
            {
                "claim_id": "claim-1",
                "verification_status": "supported",
                "slot_ids": ["definition"],
                "source_document_id": "source-1",
                "support_level": "strong",
            }
        ],
    )

    assert evidence_summary["total_candidates"] == 3
    assert evidence_summary["accepted_candidates"] == 1
    assert evidence_summary["top_rejection_reasons"] == [{"reason": "off_intent", "count": 1}]
    definition = next(row for row in slot_summary if row["slot_id"] == "definition")
    mechanism = next(row for row in slot_summary if row["slot_id"] == "mechanism")
    assert definition["status"] == "covered"
    assert mechanism["status"] == "weak"


def test_source_yield_summary_uses_taxonomy_and_contribution_levels() -> None:
    assert "unsupported_content_type" in DROPPED_SOURCE_REASONS
    assert normalize_dropped_reasons(["fetch_failed", "custom_reason"]) == (
        "fetch_failed",
        "unknown",
    )
    assert (
        contribution_level_for_counts(
            accepted_evidence_count=0,
            claim_count=0,
            candidate_count=2,
        )
        == "low"
    )
    row = SourceYieldSummary(
        source_document_id="source-1",
        url="https://example.com",
        source_intent="generic_article",
        attempted=True,
        fetched=True,
        parsed=True,
        indexed=True,
        candidate_count=2,
        accepted_evidence_count=0,
        claim_count=0,
        rejected_count=2,
        dropped_reasons=("evidence_rejected",),
        contribution_level="low",
    ).to_payload()

    assert row["dropped_reasons"] == ["evidence_rejected"]
    assert row["contribution_level"] == "low"


def test_quality_diagnostic_contract_fields_are_used_across_surfaces() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    surfaces = [
        repo_root / "services/orchestrator/app/api/schemas/research_tasks.py",
        repo_root / "services/orchestrator/app/api/routes/research_tasks.py",
        repo_root / "services/orchestrator/app/reporting/manifest.py",
        repo_root / "scripts/benchmark_queries.py",
        repo_root / "apps/web/src/types/api.ts",
        repo_root / "docs/api.md",
    ]
    surface_text = "\n".join(path.read_text() for path in surfaces)

    for field_name in QUALITY_DIAGNOSTIC_FIELDS:
        assert field_name in surface_text


def test_evidence_lineage_contract_fields_are_documented() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    schema_doc = (repo_root / "docs/schema.md").read_text()
    claims_service = (repo_root / "services/orchestrator/app/services/claims.py").read_text()

    for field_name in EVIDENCE_LINEAGE_FIELDS:
        assert field_name in schema_doc
        assert field_name in claims_service
