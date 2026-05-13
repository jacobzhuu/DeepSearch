"""Strictly scoped repository README normalized claim verification."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from packages.db.models import SourceChunk, SourceDocument
from packages.db.repositories import (
    ClaimEvidenceRepository,
    SourceChunkRepository,
    SourceDocumentRepository,
)
from services.orchestrator.app.claims import (
    CLAIM_EVIDENCE_RELATION_CANDIDATE_SUPPORT,
    CLAIM_VERIFICATION_STATUS_SUPPORTED,
)
from services.orchestrator.app.claims.verification import (
    VERIFIER_METHOD_README_REPOSITORY_NORMALIZED_COMPOSITE,
    select_verification_span,
    try_repository_readme_normalized_composite_verification,
)
from services.orchestrator.app.services.claims import (
    _claim_eligible_for_readme_repository_normalized_composite,
    create_claim_drafting_service,
)
from services.orchestrator.app.services.research_tasks import create_research_task_service


def test_composite_heading_bullet_readme_supports_normalized_claim() -> None:
    readme = (
        "## Workflow lifecycle\n"
        "LangGraph coordinates durable multi-step workflows.\n"
        "- StateGraph defines nodes and edges\n"
        "- Checkpointing enables durable execution\n"
        "- Human-in-the-loop interrupts are supported\n"
    )
    statement = (
        "LangGraph workflow lifecycle covers StateGraph nodes and edges, "
        "checkpointing for durable execution, and human-in-the-loop review."
    )
    start = 0
    end = len(readme)
    match, diag = try_repository_readme_normalized_composite_verification(
        source_text=readme,
        statement=statement,
        draft_excerpt=readme[start:end],
        start_offset=start,
        end_offset=end,
        query="What is LangGraph and how does it work?",
    )
    assert match is not None
    assert match.verifier_method == VERIFIER_METHOD_README_REPOSITORY_NORMALIZED_COMPOSITE
    assert match.relation_type == "support"
    assert diag.get("repository_normalized_support_rejection") is None
    hits = diag.get("repository_normalized_support_token_hits")
    assert isinstance(hits, list) and len(hits) >= 2


def test_composite_rejects_claim_with_missing_feature_term() -> None:
    readme = (
        "## Persistence\n"
        "LangGraph persists conversation and graph state.\n"
        "- Checkpoints store graph state\n"
        "- Threads group related runs\n"
    )
    statement = (
        "LangGraph persistence provides checkpoints, threads, and built-in "
        "blockchain anchoring for tamper evidence."
    )
    start = 0
    end = len(readme)
    match, diag = try_repository_readme_normalized_composite_verification(
        source_text=readme,
        statement=statement,
        draft_excerpt=readme[start:end],
        start_offset=start,
        end_offset=end,
        query="What is LangGraph and how does it work?",
    )
    assert match is None
    assert diag.get("repository_normalized_support_rejection") == "insufficient_token_overlap"


def test_composite_not_used_for_non_technical_explanation_query() -> None:
    readme = "## Features\n- Stateful graphs\n"
    statement = "LangGraph offers stateful graphs for workflows."
    match, diag = try_repository_readme_normalized_composite_verification(
        source_text=readme,
        statement=statement,
        draft_excerpt=readme,
        start_offset=0,
        end_offset=len(readme),
        query="How do I deploy LangGraph to Kubernetes with Docker?",
    )
    assert match is None
    assert diag.get("repository_normalized_support_rejection") == "query_not_technical_explanation"


def test_lexical_exact_support_unchanged() -> None:
    text = "LangGraph is a library for building stateful, multi-actor applications with LLMs."
    statement = "LangGraph is a library for building stateful, multi-actor applications with LLMs."
    match = select_verification_span(text, statement)
    assert match is not None
    assert match.verifier_method == "lexical_overlap_contradiction_scan_v2"


def _stub_chunk(
    *,
    chunk_id,
    url: str,
    domain: str = "raw.githubusercontent.com",
) -> SimpleNamespace:
    doc = SimpleNamespace(domain=domain, canonical_url=url)
    return SimpleNamespace(id=chunk_id, source_document=doc)


def _stub_claim(*, notes: dict) -> SimpleNamespace:
    return SimpleNamespace(notes_json=notes)


def test_eligibility_accepts_github_readme_or_repo_intent() -> None:
    cid = uuid4()
    chunk = _stub_chunk(
        chunk_id=cid,
        url="https://raw.githubusercontent.com/org/repo/main/README.md",
    )
    claim = _stub_claim(
        notes={
            "normalized_from_readme": True,
            "source_role": "official_repository",
            "source_intent": "github_readme_or_repo",
            "source_chunk_id": str(cid),
        }
    )
    assert _claim_eligible_for_readme_repository_normalized_composite(
        claim, None, chunk  # type: ignore[arg-type]
    )


def test_eligibility_requires_official_repository_readme_metadata() -> None:
    cid = uuid4()
    chunk = _stub_chunk(
        chunk_id=cid,
        url="https://raw.githubusercontent.com/org/repo/main/README.md",
    )
    claim = _stub_claim(
        notes={
            "normalized_from_readme": True,
            "source_role": "generic_article",
            "source_intent": "official_repository_readme",
            "source_chunk_id": str(cid),
        }
    )
    assert not _claim_eligible_for_readme_repository_normalized_composite(
        claim, None, chunk  # type: ignore[arg-type]
    )


def test_eligibility_requires_raw_readme_url() -> None:
    cid = uuid4()
    chunk = _stub_chunk(
        chunk_id=cid,
        url="https://raw.githubusercontent.com/org/repo/main/LICENSE.md",
    )
    claim = _stub_claim(
        notes={
            "normalized_from_readme": True,
            "source_role": "official_repository",
            "source_intent": "official_repository_readme",
            "source_chunk_id": str(cid),
        }
    )
    assert not _claim_eligible_for_readme_repository_normalized_composite(
        claim, None, chunk  # type: ignore[arg-type]
    )


def test_eligibility_requires_matching_source_chunk_id() -> None:
    chunk = _stub_chunk(
        chunk_id=uuid4(),
        url="https://raw.githubusercontent.com/org/repo/main/README.md",
    )
    claim = _stub_claim(
        notes={
            "normalized_from_readme": True,
            "source_role": "official_repository",
            "source_intent": "official_repository_readme",
            "source_chunk_id": str(uuid4()),
        }
    )
    assert not _claim_eligible_for_readme_repository_normalized_composite(
        claim, None, chunk  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("domain", ["github.com", "docs.langchain.com"])
def test_eligibility_raw_github_domain_only(domain: str) -> None:
    cid = uuid4()
    chunk = _stub_chunk(
        chunk_id=cid,
        url="https://raw.githubusercontent.com/org/repo/main/README.md",
        domain=domain,
    )
    claim = _stub_claim(
        notes={
            "normalized_from_readme": True,
            "source_role": "official_repository",
            "source_intent": "official_repository_readme",
            "source_chunk_id": str(cid),
        }
    )
    eligible = domain == "raw.githubusercontent.com"
    assert (
        _claim_eligible_for_readme_repository_normalized_composite(
            claim, None, chunk  # type: ignore[arg-type]
        )
        is eligible
    )


def test_eligibility_raw_github_readme_positive() -> None:
    cid = uuid4()
    chunk = _stub_chunk(
        chunk_id=cid,
        url="https://raw.githubusercontent.com/org/repo/main/README.md",
        domain="raw.githubusercontent.com",
    )
    claim = _stub_claim(
        notes={
            "normalized_from_readme": True,
            "source_role": "official_repository",
            "source_intent": "official_repository_readme",
            "source_chunk_id": str(cid),
        }
    )
    assert _claim_eligible_for_readme_repository_normalized_composite(
        claim, None, chunk  # type: ignore[arg-type]
    )


def test_end_to_end_readme_normalized_claim_supported_via_composite(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is LangGraph and how does it work?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md",
            domain="raw.githubusercontent.com",
            title="README",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=0.9,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.9,
        )
    )
    chunk_text = (
        "## Workflow\n"
        "- LangGraph StateGraph nodes and edges\n"
        "- Checkpoint and resume\n"
        "- Human-in-the-loop review\n"
    )
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=chunk_text,
            token_count=40,
            metadata_json={"strategy": "paragraph_window_v1", "eligible_for_claims": True},
        )
    )
    db_session.commit()

    from tests.unit.orchestrator.test_claim_verification_service import InMemoryChunkIndexBackend

    service = create_claim_drafting_service(
        db_session,
        index_backend=InMemoryChunkIndexBackend(hits=[]),
        max_candidates_per_request=5,
        verification_max_claims_per_request=5,
        retrieval_max_results_per_request=5,
    )
    draft = service.draft_claims(
        task.id,
        query=task.query,
        source_chunk_ids=[source_chunk.id],
        limit=8,
    )
    norm = [e for e in draft.entries if e.claim.notes_json.get("normalized_from_readme") is True]
    assert norm
    norm_ids = [e.claim.id for e in norm]
    verify = service.verify_claims(task.id, claim_ids=norm_ids, limit=8)
    assert (
        verify.readme_normalized_verification["repository_normalized_verification_attempt_count"]
        >= 1
    )
    supported_norm = [
        e
        for e in verify.entries
        if (e.claim.notes_json or {}).get("normalized_from_readme") is True
        and e.claim.verification_status == CLAIM_VERIFICATION_STATUS_SUPPORTED
    ]
    assert supported_norm
    verification_notes = supported_norm[0].claim.notes_json.get("verification") or {}
    rels = verification_notes.get("evidence_relations") or []
    methods = {r.get("verifier_method") for r in rels if isinstance(r, dict)}
    assert VERIFIER_METHOD_README_REPOSITORY_NORMALIZED_COMPOSITE in methods

    cand_rows = ClaimEvidenceRepository(db_session).list_for_task(task.id)
    assert any(r.relation_type == CLAIM_EVIDENCE_RELATION_CANDIDATE_SUPPORT for r in cand_rows)


def test_non_normalized_claim_does_not_increment_readme_composite_counters(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is LangGraph and how does it work?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.com/page",
            domain="example.com",
            title="Example",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=0.5,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.5,
        )
    )
    text = "LangGraph is a library for building stateful, multi-actor applications with LLMs."
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=text,
            token_count=20,
            metadata_json={"eligible_for_claims": True},
        )
    )
    db_session.commit()
    from tests.unit.orchestrator.test_claim_verification_service import (
        InMemoryChunkIndexBackend,
        _indexed_hit,
    )

    backend = InMemoryChunkIndexBackend(
        hits=[
            _indexed_hit(
                task_id=task.id,
                source_document_id=source_document.id,
                source_chunk_id=source_chunk.id,
                canonical_url=source_document.canonical_url,
                domain=source_document.domain,
                text=text,
                score=1.0,
            )
        ]
    )
    service = create_claim_drafting_service(
        db_session,
        index_backend=backend,
        max_candidates_per_request=3,
        verification_max_claims_per_request=3,
        retrieval_max_results_per_request=3,
    )
    service.draft_claims(task.id, query=task.query, source_chunk_ids=[source_chunk.id], limit=3)
    verify = service.verify_claims(task.id, claim_ids=None, limit=3)
    assert (
        verify.readme_normalized_verification["repository_normalized_verification_attempt_count"]
        == 0
    )
    assert (
        verify.readme_normalized_verification["repository_normalized_verification_supported_count"]
        == 0
    )
