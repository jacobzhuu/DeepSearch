from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.orchestrator.app.claims.verification import (
    VERIFIER_METHOD_README_REPOSITORY_NORMALIZED_COMPOSITE,
)
from services.orchestrator.app.reporting.markdown import ReportClaimItem, ReportEvidenceItem
from services.orchestrator.app.research_quality.readme_normalized_signals import (
    is_raw_github_readme_url,
    normalized_from_readme_from_notes,
    readme_composite_support_relation_count_for_claims,
    readme_composite_support_relation_count_from_notes,
    readme_composite_support_relation_count_from_report_claims,
    report_evidence_is_readme_composite,
)


def test_normalized_from_readme_nested_evidence_candidate_metadata() -> None:
    notes = {
        "evidence_candidate": {
            "metadata": {"normalized_from_readme": True},
        }
    }
    assert normalized_from_readme_from_notes(notes) is True


def test_normalized_from_readme_top_level_false_nested_true() -> None:
    notes = {
        "normalized_from_readme": False,
        "evidence_candidate": {"normalized_from_readme": True},
    }
    assert normalized_from_readme_from_notes(notes) is True


def test_readme_composite_count_from_verification_relations() -> None:
    notes = {
        "verification": {
            "evidence_relations": [
                {
                    "relation_type": "support",
                    "verifier_method": VERIFIER_METHOD_README_REPOSITORY_NORMALIZED_COMPOSITE,
                },
                {
                    "relation_type": "support",
                    "verifier_method": VERIFIER_METHOD_README_REPOSITORY_NORMALIZED_COMPOSITE,
                },
                {"relation_type": "contradict", "verifier_method": "other"},
            ]
        }
    }
    assert readme_composite_support_relation_count_from_notes(notes) == 2


def test_readme_composite_count_reasons_fallback() -> None:
    notes = {
        "verification": {
            "evidence_relations": [
                {
                    "relation_type": "weak_support",
                    "reasons": ["readme_repository_normalized_composite"],
                }
            ]
        }
    }
    assert readme_composite_support_relation_count_from_notes(notes) == 1


def test_readme_composite_for_claims_aggregates() -> None:
    c1 = SimpleNamespace(
        notes_json={
            "verification": {
                "evidence_relations": [
                    {
                        "relation_type": "support",
                        "relation_detail": "readme_composite_token_support",
                    }
                ]
            }
        }
    )
    c2 = SimpleNamespace(notes_json={})
    assert readme_composite_support_relation_count_for_claims([c1, c2]) == 1


def test_is_raw_github_readme_url() -> None:
    assert is_raw_github_readme_url(
        "https://raw.githubusercontent.com/org/repo/main/README.md"
    )
    assert not is_raw_github_readme_url("https://github.com/org/repo/blob/main/README.md")


def _minimal_report_evidence(**kwargs: object) -> ReportEvidenceItem:
    base = dict(
        claim_evidence_id=uuid4(),
        citation_span_id=uuid4(),
        source_document_id=uuid4(),
        source_chunk_id=uuid4(),
        relation_type="support",
        score=0.9,
        canonical_url="https://raw.githubusercontent.com/o/r/main/README.md",
        domain="raw.githubusercontent.com",
        chunk_no=0,
        start_offset=0,
        end_offset=4,
        excerpt="test",
    )
    base.update(kwargs)
    return ReportEvidenceItem(**base)


def test_report_evidence_readme_composite() -> None:
    ev = _minimal_report_evidence(
        verifier_method=VERIFIER_METHOD_README_REPOSITORY_NORMALIZED_COMPOSITE
    )
    assert report_evidence_is_readme_composite(ev) is True


def test_readme_composite_count_from_report_claims() -> None:
    cid = uuid4()
    claim = ReportClaimItem(
        claim_id=cid,
        statement="s",
        claim_type="fact",
        confidence=0.9,
        verification_status="supported",
        rationale=None,
        support_evidence=[
            _minimal_report_evidence(
                verifier_method=VERIFIER_METHOD_README_REPOSITORY_NORMALIZED_COMPOSITE
            ),
            _minimal_report_evidence(verifier_method="lexical_overlap_contradiction_scan_v2"),
        ],
        contradict_evidence=[],
    )
    assert readme_composite_support_relation_count_from_report_claims([claim]) == 1


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://RAW.GITHUBUSERCONTENT.COM/x/y/refs/heads/main/readme.md", True),
        ("https://raw.githubusercontent.com/a/b/v1.0.0/README.markdown", True),
        ("https://example.com/README.md", False),
    ],
)
def test_raw_readme_url_param(url: str, expected: bool) -> None:
    assert is_raw_github_readme_url(url) is expected
