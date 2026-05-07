from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.db.models import (
    CitationSpan,
    Claim,
    ClaimEvidence,
    ReportArtifact,
    ResearchTask,
    SourceChunk,
    SourceDocument,
)
from packages.db.repositories import ReportArtifactRepository
from packages.db.repositories.sources import SourceChunkRepository, SourceDocumentRepository
from services.orchestrator.app.indexing import (
    ChunkIndexDocument,
    IndexedChunkPage,
)
from services.orchestrator.app.llm import LLMRequest, LLMResponse
from services.orchestrator.app.services.claims import create_claim_drafting_service
from services.orchestrator.app.services.debug_pipeline import (
    _claim_limit_for_query,
    _select_claim_drafting_chunk_ids,
)
from services.orchestrator.app.services.reporting import (
    ReportArtifactContentMismatchError,
    create_report_synthesis_service,
)
from services.orchestrator.app.services.research_tasks import create_research_task_service
from services.orchestrator.app.storage import FilesystemSnapshotObjectStore


class FakeReportLLMProvider:
    name = "fake-report-llm"

    def __init__(self, response_payload: dict[str, object]) -> None:
        self.response_payload = response_payload
        self.requests: list[LLMRequest] = []

    def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return LLMResponse(
            text=json.dumps(self.response_payload),
            model=request.model or "fake-report-model",
            provider=self.name,
            usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            raw_response_id="fake-response",
            finish_reason="stop",
        )


class EmptyChunkIndexBackend:
    def validate_configuration(self) -> None:
        return None

    def ensure_index(self) -> None:
        return None

    def upsert_chunks(self, documents: list[ChunkIndexDocument]) -> None:
        del documents

    def list_chunks(self, *, task_id: UUID, offset: int, limit: int) -> IndexedChunkPage:
        del task_id, offset, limit
        return IndexedChunkPage(total=0, hits=[])

    def retrieve_chunks(
        self,
        *,
        task_id: UUID,
        query: str,
        offset: int,
        limit: int,
    ) -> IndexedChunkPage:
        del task_id, query, offset, limit
        return IndexedChunkPage(total=0, hits=[])


def _manifest(artifact: ReportArtifact) -> dict[str, Any]:
    assert artifact.manifest_json is not None
    return artifact.manifest_json


def test_deployment_pipeline_claim_limit_allows_more_than_slot_count() -> None:
    assert _claim_limit_for_query("How to deploy SearXNG with Docker?", 5) == 16


def test_report_synthesis_service_generates_and_reuses_markdown_artifact(
    db_session: Session,
    tmp_path: Path,
) -> None:
    seeded = _seed_verified_claims(db_session)
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    first_result = service.generate_markdown_report(seeded.task_id)
    second_result = service.generate_markdown_report(seeded.task_id)
    latest_result = service.get_latest_markdown_report(seeded.task_id)

    artifacts = ReportArtifactRepository(db_session).list_for_task(
        seeded.task_id, format="markdown"
    )
    stored_bytes = object_store.get_bytes(
        bucket=first_result.artifact.storage_bucket,
        key=first_result.artifact.storage_key,
    )

    assert first_result.artifact.version == 1
    assert first_result.reused_existing is False
    assert second_result.reused_existing is True
    assert second_result.artifact.id == first_result.artifact.id
    assert latest_result.artifact.id == first_result.artifact.id
    assert len(artifacts) == 1
    assert first_result.supported_claims == 1
    assert first_result.mixed_claims == 1
    assert first_result.contradicted_claims == 0
    assert first_result.unsupported_claims == 1
    assert first_result.artifact.content_hash == _markdown_hash(first_result.markdown)
    assert first_result.artifact.manifest_json is not None
    assert first_result.artifact.manifest_json["manifest_version"] == 1
    assert first_result.artifact.manifest_json["claim_counts"]["supported"] == 1
    assert first_result.artifact.manifest_json["claim_counts"]["mixed"] == 1
    assert first_result.artifact.manifest_json["claim_counts"]["contradicted"] == 0
    assert first_result.artifact.manifest_json["claim_counts"]["unsupported"] == 1
    assert "slot_coverage_summary" in first_result.artifact.manifest_json
    assert "source_yield_summary" in first_result.artifact.manifest_json
    assert "evidence_yield_summary" in first_result.artifact.manifest_json
    assert "verification_summary" in first_result.artifact.manifest_json
    assert "## Executive Summary" in first_result.markdown
    assert "## Answer" in first_result.markdown
    assert "## Answer Slot Coverage" in first_result.markdown
    assert "## Appendix: Claim Evidence Mapping" not in first_result.markdown
    assert "The mixed claim remains under dispute." in first_result.markdown
    assert "The unsupported claim currently lacks support evidence." in first_result.markdown
    assert stored_bytes.decode("utf-8") == first_result.markdown


def test_report_synthesis_service_can_use_grounded_llm_writer(
    db_session: Session,
    tmp_path: Path,
) -> None:
    seeded = _seed_verified_claims(db_session)
    claim, evidence = _first_supported_claim_and_evidence(db_session, seeded.task_id)
    provider = FakeReportLLMProvider(
        {
            "title": "Grounded synthesized report",
            "executive_summary": [
                {
                    "text": "The supported position is grounded in the cited ledger evidence.",
                    "claim_ids": [str(claim.id)],
                    "claim_evidence_ids": [str(evidence.id)],
                    "citation_span_ids": [str(evidence.citation_span_id)],
                }
            ],
            "sections": [
                {
                    "heading": "Verified finding",
                    "items": [
                        {
                            "text": "This finding is tied to the verified claim.",
                            "claim_ids": [str(claim.id)],
                            "claim_evidence_ids": [str(evidence.id)],
                            "citation_span_ids": [str(evidence.citation_span_id)],
                        }
                    ],
                }
            ],
            "uncertainties": [],
            "unresolved": [],
        }
    )
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
        llm_provider=provider,
        llm_model="fake-report-model",
        llm_report_writer_enabled=True,
    )

    result = service.generate_markdown_report(seeded.task_id)

    assert provider.requests
    assert provider.requests[0].metadata["purpose"] == "grounded_report_writer"
    assert result.writer_mode == "llm_grounded"
    assert result.llm_writer_status == "used"
    assert result.title == "Grounded synthesized report"
    assert "The supported position is grounded in the cited ledger evidence." in result.markdown
    assert str(claim.id) not in result.markdown
    assert str(evidence.id) not in result.markdown
    assert "Appendix: Claim Evidence Mapping" not in result.markdown
    manifest = _manifest(result.artifact)
    assert manifest["report_writer"]["mode"] == "llm_grounded"
    assert manifest["report_writer"]["status"] == "used"


def test_grounded_llm_writer_honors_ledger_debug_appendix_flag(
    db_session: Session,
    tmp_path: Path,
) -> None:
    seeded = _seed_verified_claims(db_session)
    claim, evidence = _first_supported_claim_and_evidence(db_session, seeded.task_id)
    provider = FakeReportLLMProvider(
        {
            "title": "Grounded synthesized report",
            "executive_summary": [
                {
                    "text": "The supported position is grounded in the cited ledger evidence.",
                    "claim_ids": [str(claim.id)],
                    "claim_evidence_ids": [str(evidence.id)],
                    "citation_span_ids": [str(evidence.citation_span_id)],
                }
            ],
            "sections": [],
            "uncertainties": [],
            "unresolved": [],
        }
    )
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
        llm_provider=provider,
        llm_model="fake-report-model",
        llm_report_writer_enabled=True,
        include_ledger_debug_appendix=True,
    )

    result = service.generate_markdown_report(seeded.task_id)
    manifest = _manifest(result.artifact)

    assert result.writer_mode == "llm_grounded"
    assert "## Appendix: Claim Evidence Mapping" in result.markdown
    assert str(claim.id) in result.markdown
    assert str(evidence.id) in result.markdown
    assert str(evidence.citation_span_id) in result.markdown
    assert manifest["report_writer"]["include_ledger_debug_appendix"] is True


def test_grounded_llm_writer_uses_selected_chinese_report_language(
    db_session: Session,
    tmp_path: Path,
) -> None:
    seeded = _seed_verified_claims(db_session)
    task = db_session.get(ResearchTask, seeded.task_id)
    assert task is not None
    task.constraints_json = {"report_language": "zh-CN"}
    db_session.commit()
    claim, evidence = _first_supported_claim_and_evidence(db_session, seeded.task_id)
    provider = FakeReportLLMProvider(
        {
            "title": "中文证据报告",
            "executive_summary": [
                {
                    "text": "这个结论只使用已验证证据。",
                    "claim_ids": [str(claim.id)],
                    "claim_evidence_ids": [str(evidence.id)],
                    "citation_span_ids": [str(evidence.citation_span_id)],
                }
            ],
            "sections": [],
            "uncertainties": [],
            "unresolved": [],
        }
    )
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
        llm_provider=provider,
        llm_model="fake-report-model",
        llm_report_writer_enabled=True,
    )

    result = service.generate_markdown_report(seeded.task_id)
    manifest = _manifest(result.artifact)
    request = provider.requests[0]

    assert "Simplified Chinese" in request.system_prompt
    assert request.metadata["report_language"] == "zh-CN"
    assert '"report_language": "zh-CN"' in request.user_prompt
    assert result.report_language == "zh-CN"
    assert result.writer_mode == "llm_grounded"
    assert "# 中文证据报告" in result.markdown
    assert "## 执行摘要" in result.markdown
    assert manifest["report_language"] == "zh-CN"
    assert manifest["report_writer"]["report_language"] == "zh-CN"


def test_grounded_llm_writer_falls_back_when_chinese_request_returns_english(
    db_session: Session,
    tmp_path: Path,
) -> None:
    seeded = _seed_verified_claims(db_session)
    task = db_session.get(ResearchTask, seeded.task_id)
    assert task is not None
    task.constraints_json = {"report_language": "zh-CN"}
    db_session.commit()
    claim, evidence = _first_supported_claim_and_evidence(db_session, seeded.task_id)
    provider = FakeReportLLMProvider(
        {
            "title": "English report",
            "executive_summary": [
                {
                    "text": "This sentence ignores the requested report language.",
                    "claim_ids": [str(claim.id)],
                    "claim_evidence_ids": [str(evidence.id)],
                    "citation_span_ids": [str(evidence.citation_span_id)],
                }
            ],
            "sections": [],
            "uncertainties": [],
            "unresolved": [],
        }
    )
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
        llm_provider=provider,
        llm_model="fake-report-model",
        llm_report_writer_enabled=True,
    )

    result = service.generate_markdown_report(seeded.task_id)

    assert result.writer_mode == "deterministic"
    assert result.llm_writer_status == "fallback_after_llm_validation_error"
    assert "## 执行摘要" in result.markdown
    assert "This sentence ignores" not in result.markdown


def test_report_synthesis_service_falls_back_when_llm_ids_are_not_grounded(
    db_session: Session,
    tmp_path: Path,
) -> None:
    seeded = _seed_verified_claims(db_session)
    provider = FakeReportLLMProvider(
        {
            "title": "Invalid ungrounded report",
            "executive_summary": [
                {
                    "text": "This unsupported LLM-only sentence must not be rendered.",
                    "claim_ids": ["not-a-real-claim"],
                    "claim_evidence_ids": ["not-real-evidence"],
                    "citation_span_ids": ["not-real-citation"],
                }
            ],
            "sections": [],
            "uncertainties": [],
            "unresolved": [],
        }
    )
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
        llm_provider=provider,
        llm_model="fake-report-model",
        llm_report_writer_enabled=True,
    )

    result = service.generate_markdown_report(seeded.task_id)

    assert result.writer_mode == "deterministic"
    assert result.llm_writer_status == "fallback_after_llm_validation_error"
    assert "This unsupported LLM-only sentence must not be rendered." not in result.markdown
    assert "## Executive Summary" in result.markdown
    assert (
        _manifest(result.artifact)["report_writer"]["status"]
        == "fallback_after_llm_validation_error"
    )


def test_get_latest_report_returns_stored_artifact_even_if_ledger_later_changes(
    db_session: Session,
    tmp_path: Path,
) -> None:
    seeded = _seed_verified_claims(db_session)
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    first_result = service.generate_markdown_report(seeded.task_id)
    task = db_session.get(ResearchTask, seeded.task_id)
    assert task is not None
    task.query = "A later task query that should not rewrite the stored artifact title"
    db_session.commit()

    latest_result = service.get_latest_markdown_report(seeded.task_id)

    assert latest_result.artifact.id == first_result.artifact.id
    assert latest_result.title == first_result.title
    assert latest_result.markdown == first_result.markdown
    assert latest_result.supported_claims == 0
    assert latest_result.mixed_claims == 0
    assert latest_result.contradicted_claims == 0
    assert latest_result.unsupported_claims == 0
    assert latest_result.draft_claims == 0


def test_report_synthesis_accepts_legacy_claim_notes_without_lineage(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is OpenSearch and how does it work?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://docs.opensearch.org/latest/getting-started/intro/",
            domain="docs.opensearch.org",
            title="Intro to OpenSearch",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=None,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=None,
        )
    )
    chunk_text = (
        "OpenSearch is a distributed search and analytics engine that lets users store, "
        "search, and analyze data."
    )
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=chunk_text,
            token_count=18,
            metadata_json={"strategy": "paragraph_window_v1", "content_quality_score": 0.9},
        )
    )
    legacy_claim = Claim(
        task_id=task.id,
        statement=chunk_text,
        claim_type="fact",
        confidence=0.9,
        verification_status="supported",
        notes_json={},
    )
    db_session.add(legacy_claim)
    db_session.flush()
    span = CitationSpan(
        source_chunk_id=source_chunk.id,
        start_offset=0,
        end_offset=len(chunk_text),
        excerpt=chunk_text,
        normalized_excerpt_hash="sha256:legacy-opensearch",
    )
    db_session.add(span)
    db_session.flush()
    db_session.add(
        ClaimEvidence(
            claim_id=legacy_claim.id,
            citation_span_id=span.id,
            relation_type="support",
            score=0.9,
        )
    )
    db_session.commit()
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    result = service.generate_markdown_report(task.id)

    assert result.supported_claims == 1
    assert chunk_text in result.markdown
    manifest = _manifest(result.artifact)
    assert manifest["source_yield_summary"][0]["contribution_level"] == "medium"
    assert manifest["evidence_yield_summary"]["accepted_candidates"] == 1
    assert manifest["verification_summary"]["strong_supported_claim_count"] == 1
    definition_slot = next(
        row for row in manifest["slot_coverage_summary"] if row["slot_id"] == "definition"
    )
    assert definition_slot["status"] == "covered"


def test_get_latest_report_detects_content_hash_mismatch(
    db_session: Session,
    tmp_path: Path,
) -> None:
    seeded = _seed_verified_claims(db_session)
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    result = service.generate_markdown_report(seeded.task_id)
    object_store.put_bytes(
        bucket=result.artifact.storage_bucket,
        key=result.artifact.storage_key,
        content=b"tampered report bytes",
        content_type="text/markdown",
    )

    with pytest.raises(ReportArtifactContentMismatchError):
        service.get_latest_markdown_report(seeded.task_id)


def test_report_synthesis_filters_bad_claims_and_short_evidence(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="Explain OpenAI",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.com/weak",
            domain="example.com",
            title="Weak source",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=None,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=None,
        )
    )
    weak_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text="C",
            token_count=1,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    bad_claim = Claim(
        task_id=task.id,
        statement="Data",
        claim_type="fact",
        confidence=0.9,
        verification_status="supported",
        notes_json={"verification": {"rationale": "Found 1 support evidence."}},
    )
    db_session.add(bad_claim)
    db_session.flush()
    weak_span = CitationSpan(
        source_chunk_id=weak_chunk.id,
        start_offset=0,
        end_offset=1,
        excerpt="C",
        normalized_excerpt_hash="sha256:weak",
    )
    db_session.add(weak_span)
    db_session.flush()
    db_session.add(
        ClaimEvidence(
            claim_id=bad_claim.id,
            citation_span_id=weak_span.id,
            relation_type="support",
            score=0.9,
        )
    )
    db_session.commit()

    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    result = service.generate_markdown_report(task.id)

    assert result.supported_claims == 0
    assert "Data" not in result.markdown
    assert 'excerpt: "C"' not in result.markdown


def test_report_synthesis_excludes_low_quality_supported_claims_and_warns(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is SearXNG and how does it work?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://docs.searxng.org/user/about.html",
            domain="docs.searxng.org",
            title="SearXNG about",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=0.95,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.95,
        )
    )
    source_chunk_repo = SourceChunkRepository(db_session)
    good_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=(
                "SearXNG is a metasearch engine, aggregating the results of other search "
                "engines while not storing information about its users."
            ),
            token_count=18,
            metadata_json={"strategy": "paragraph_window_v1", "content_quality_score": 0.95},
        )
    )
    bad_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=1,
            text="Track development, send contributions, and report issues at SearXNG sources.",
            token_count=10,
            metadata_json={"strategy": "paragraph_window_v1", "content_quality_score": 0.95},
        )
    )
    good_claim = Claim(
        task_id=task.id,
        statement=good_chunk.text,
        claim_type="fact",
        confidence=0.93,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "definition",
            "claim_quality_score": 0.95,
            "query_answer_score": 1.0,
        },
    )
    bad_claim = Claim(
        task_id=task.id,
        statement=bad_chunk.text,
        claim_type="fact",
        confidence=0.9,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "community",
            "claim_quality_score": 0.2,
            "query_answer_score": 0.1,
        },
    )
    db_session.add_all([good_claim, bad_claim])
    db_session.flush()
    good_span = CitationSpan(
        source_chunk_id=good_chunk.id,
        start_offset=0,
        end_offset=len(good_chunk.text),
        excerpt=good_chunk.text,
        normalized_excerpt_hash="sha256:good-searxng",
    )
    bad_span = CitationSpan(
        source_chunk_id=bad_chunk.id,
        start_offset=0,
        end_offset=len(bad_chunk.text),
        excerpt=bad_chunk.text,
        normalized_excerpt_hash="sha256:bad-searxng",
    )
    db_session.add_all([good_span, bad_span])
    db_session.flush()
    db_session.add_all(
        [
            ClaimEvidence(
                claim_id=good_claim.id,
                citation_span_id=good_span.id,
                relation_type="support",
                score=0.93,
            ),
            ClaimEvidence(
                claim_id=bad_claim.id,
                citation_span_id=bad_span.id,
                relation_type="support",
                score=0.9,
            ),
        ]
    )
    db_session.commit()
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    result = service.generate_markdown_report(task.id)

    assert result.supported_claims == 1
    assert good_chunk.text in result.markdown
    assert bad_chunk.text not in result.markdown
    assert (
        "Coverage is limited because no mechanism/privacy/feature claims were generated."
        in result.markdown
    )
    assert "Answer-relevant claims included: 1." in result.markdown
    assert "Excluded low-quality or off-query claims: 1." in result.markdown


def test_report_synthesis_excludes_supported_other_and_setup_claims(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is SearXNG and how does it work?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://docs.searxng.org/",
            domain="docs.searxng.org",
            title="SearXNG docs",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=0.95,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.95,
        )
    )
    source_chunk_repo = SourceChunkRepository(db_session)
    good_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text="SearXNG is a metasearch engine that aggregates results from search engines.",
            token_count=16,
            metadata_json={"strategy": "paragraph_window_v1", "content_quality_score": 0.95},
        )
    )
    other_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=1,
            text="SearXNG has a public instances list.",
            token_count=8,
            metadata_json={"strategy": "paragraph_window_v1", "content_quality_score": 0.95},
        )
    )
    setup_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=2,
            text="Get started with SearXNG by using one of the instances listed at .",
            token_count=14,
            metadata_json={"strategy": "paragraph_window_v1", "content_quality_score": 0.95},
        )
    )
    good_claim = Claim(
        task_id=task.id,
        statement=good_chunk.text,
        claim_type="fact",
        confidence=0.93,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "definition",
            "claim_quality_score": 0.95,
            "query_answer_score": 1.0,
        },
    )
    other_claim = Claim(
        task_id=task.id,
        statement=other_chunk.text,
        claim_type="fact",
        confidence=0.88,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "other",
            "claim_quality_score": 0.8,
            "query_answer_score": 0.55,
        },
    )
    setup_claim = Claim(
        task_id=task.id,
        statement=setup_chunk.text,
        claim_type="fact",
        confidence=0.88,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "setup",
            "claim_quality_score": 0.8,
            "query_answer_score": 0.55,
        },
    )
    db_session.add_all([good_claim, other_claim, setup_claim])
    db_session.flush()
    spans = [
        CitationSpan(
            source_chunk_id=good_chunk.id,
            start_offset=0,
            end_offset=len(good_chunk.text),
            excerpt=good_chunk.text,
            normalized_excerpt_hash="sha256:good-definition",
        ),
        CitationSpan(
            source_chunk_id=other_chunk.id,
            start_offset=0,
            end_offset=len(other_chunk.text),
            excerpt=other_chunk.text,
            normalized_excerpt_hash="sha256:other-off-query",
        ),
        CitationSpan(
            source_chunk_id=setup_chunk.id,
            start_offset=0,
            end_offset=len(setup_chunk.text),
            excerpt=setup_chunk.text,
            normalized_excerpt_hash="sha256:setup-instruction",
        ),
    ]
    db_session.add_all(spans)
    db_session.flush()
    db_session.add_all(
        [
            ClaimEvidence(
                claim_id=good_claim.id,
                citation_span_id=spans[0].id,
                relation_type="support",
                score=0.93,
            ),
            ClaimEvidence(
                claim_id=other_claim.id,
                citation_span_id=spans[1].id,
                relation_type="support",
                score=0.88,
            ),
            ClaimEvidence(
                claim_id=setup_claim.id,
                citation_span_id=spans[2].id,
                relation_type="support",
                score=0.88,
            ),
        ]
    )
    db_session.commit()
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    result = service.generate_markdown_report(task.id)

    assert result.supported_claims == 1
    assert good_chunk.text in result.markdown
    assert other_chunk.text not in result.markdown
    assert setup_chunk.text not in result.markdown
    assert "Excluded low-quality or off-query claims: 2." in result.markdown


def test_report_eligibility_excludes_supported_off_topic_reviewer_claims(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is TensorFlow and how does automatic differentiation work?",
        constraints={},
    )
    good_claim = _add_supported_report_claim(
        db_session,
        task,
        statement=(
            "TensorFlow is a machine learning framework whose automatic differentiation "
            "system records tensor operations to compute gradients."
        ),
        canonical_url="https://example.org/tensorflow-autodiff",
        domain="example.org",
        notes={
            "verification": {"rationale": "Found 1 support evidence."},
            "claim_category": "definition",
            "claim_quality_score": 0.95,
            "query_answer_score": 0.95,
            "slot_ids": ["definition", "mechanism"],
        },
    )
    off_topic_claim = _add_supported_report_claim(
        db_session,
        task,
        statement="FastAPI uses Python type hints to validate request data.",
        canonical_url="https://example.org/fastapi",
        domain="example.org",
        notes={
            "verification": {"rationale": "Found 1 support evidence."},
            "claim_category": "definition",
            "claim_quality_score": 0.92,
            "query_answer_score": 0.92,
            "slot_ids": ["definition"],
            "llm_claim_review": {
                "decision": "reject",
                "confidence": 0.9,
                "reasons": ["The claim answers a different framework question."],
                "covered_slot_ids": ["definition"],
            },
        },
    )
    db_session.commit()
    service = _report_service(db_session, tmp_path)

    result = service.generate_markdown_report(task.id)

    db_session.refresh(good_claim)
    db_session.refresh(off_topic_claim)
    assert good_claim.statement in result.markdown
    assert off_topic_claim.statement not in result.markdown
    assert good_claim.notes_json["report_eligible"] is True
    assert off_topic_claim.notes_json["report_eligible"] is False
    assert "claim_review_reject" in off_topic_claim.notes_json["report_eligibility"]["reasons"]
    assert "query_focus_mismatch" in off_topic_claim.notes_json["report_eligibility"]["reasons"]


def test_adjacent_entity_claims_are_excluded_unless_explicitly_requested(
    db_session: Session,
    tmp_path: Path,
) -> None:
    overview_task = create_research_task_service(db_session).create_task(
        query="What is adapter tuning in large language models?",
        constraints={},
    )
    adapter_claim = _add_supported_report_claim(
        db_session,
        overview_task,
        statement=(
            "Adapter tuning adds small trainable modules to a large language model while "
            "leaving most base parameters unchanged."
        ),
        canonical_url="https://example.org/adapter-tuning",
        domain="example.org",
        notes={
            "verification": {"rationale": "Found 1 support evidence."},
            "claim_category": "definition",
            "claim_quality_score": 0.95,
            "query_answer_score": 0.95,
            "slot_ids": ["definition"],
        },
    )
    adjacent_claim = _add_supported_report_claim(
        db_session,
        overview_task,
        statement=("Representation tuning changes hidden representations during model adaptation."),
        canonical_url="https://example.org/representation-tuning",
        domain="example.org",
        notes={
            "verification": {"rationale": "Found 1 support evidence."},
            "claim_category": "mechanism",
            "claim_quality_score": 0.9,
            "query_answer_score": 0.88,
            "slot_ids": ["mechanism"],
            "llm_claim_review": {
                "decision": "downrank",
                "confidence": 0.86,
                "reasons": ["Adjacent technique, not the requested main technique."],
                "covered_slot_ids": ["mechanism"],
            },
        },
    )
    explicit_task = create_research_task_service(db_session).create_task(
        query="What is representation tuning in large language models?",
        constraints={},
    )
    explicit_claim = _add_supported_report_claim(
        db_session,
        explicit_task,
        statement=("Representation tuning changes hidden representations during model adaptation."),
        canonical_url="https://example.org/representation-tuning-explicit",
        domain="example.org",
        notes={
            "verification": {"rationale": "Found 1 support evidence."},
            "claim_category": "definition",
            "claim_quality_score": 0.94,
            "query_answer_score": 0.94,
            "slot_ids": ["definition"],
        },
    )
    db_session.commit()
    service = _report_service(db_session, tmp_path)

    overview_result = service.generate_markdown_report(overview_task.id)
    explicit_result = service.generate_markdown_report(explicit_task.id)

    db_session.refresh(adapter_claim)
    db_session.refresh(adjacent_claim)
    db_session.refresh(explicit_claim)
    assert adapter_claim.statement in overview_result.markdown
    assert adjacent_claim.statement not in overview_result.markdown
    assert adjacent_claim.notes_json["report_eligible"] is False
    assert "claim_review_downrank" in adjacent_claim.notes_json["report_eligibility"]["reasons"]
    assert explicit_claim.statement in explicit_result.markdown
    assert explicit_claim.notes_json["report_eligible"] is True


def test_claim_drafting_chunk_selection_preserves_source_diversity_for_mechanism_questions(
    db_session: Session,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is ExampleFlow and how does routing work?",
        constraints={},
    )
    for source_index in range(3):
        source_document = SourceDocumentRepository(db_session).add(
            SourceDocument(
                task_id=task.id,
                content_snapshot_id=None,
                canonical_url=f"https://example.org/source-{source_index}",
                domain="example.org",
                title=f"Example source {source_index}",
                source_type="web_page",
                published_at=None,
                fetched_at=datetime(2026, 4, 26, 10, source_index, tzinfo=UTC),
                authority_score=0.8,
                freshness_score=None,
                originality_score=None,
                consistency_score=None,
                safety_score=None,
                final_source_score=0.8,
            )
        )
        for chunk_no in range(2):
            SourceChunkRepository(db_session).add(
                SourceChunk(
                    source_document_id=source_document.id,
                    chunk_no=chunk_no,
                    text=(
                        "ExampleFlow routing sends state between workflow nodes until the "
                        f"pipeline completes. Source {source_index}, chunk {chunk_no}."
                    ),
                    token_count=20,
                    metadata_json={
                        "strategy": "paragraph_window_v1",
                        "content_quality_score": 0.9,
                        "eligible_for_claims": True,
                    },
                )
            )
    db_session.commit()

    selected_ids = _select_claim_drafting_chunk_ids(
        db_session,
        task.id,
        query=task.query,
        limit=3,
    )
    selected_chunks = list(
        db_session.scalars(select(SourceChunk).where(SourceChunk.id.in_(selected_ids)))
    )

    assert len(selected_ids) == 3
    assert len({chunk.source_document_id for chunk in selected_chunks}) == 3


def test_deployment_report_renders_slot_sections_and_coverage_gaps(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="How to deploy SearXNG with Docker?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://docs.searxng.org/admin/installation-docker",
            domain="docs.searxng.org",
            title="Installation container",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 5, 5, 10, 0, tzinfo=UTC),
            authority_score=0.95,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.95,
        )
    )
    command = (
        "$ docker run --name searxng -d -p 8888:8080 "
        '-v "./config/:/etc/searxng/" docker.io/searxng/searxng:latest'
    )
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=command,
            token_count=24,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.9,
                "quality_reasons": ["deployment_code_or_config"],
            },
        )
    )
    statement = f"Deployment command evidence: `{command}`."
    claim = Claim(
        task_id=task.id,
        statement=statement,
        claim_type="fact",
        confidence=0.92,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "deployment/self_hosting",
            "claim_quality_score": 0.9,
            "query_answer_score": 0.9,
            "slot_ids": [
                "deployment_run_or_compose",
                "deployment_ports",
                "deployment_volumes",
            ],
            "evidence_kind": "deployment_code_or_config",
        },
    )
    db_session.add(claim)
    db_session.flush()
    span = CitationSpan(
        source_chunk_id=source_chunk.id,
        start_offset=0,
        end_offset=len(command),
        excerpt=command,
        normalized_excerpt_hash="sha256:deployment-command",
    )
    db_session.add(span)
    db_session.flush()
    db_session.add(
        ClaimEvidence(
            claim_id=claim.id,
            citation_span_id=span.id,
            relation_type="support",
            score=0.92,
        )
    )
    db_session.commit()
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    result = service.generate_markdown_report(task.id)

    assert "### Docker run / Docker Compose" in result.markdown
    assert "### Volumes" in result.markdown
    assert "### Ports" in result.markdown
    assert "### Security" in result.markdown
    assert statement in result.markdown
    assert "```" in result.markdown
    assert command in result.markdown
    assert "Answer slot coverage: 3/8." in result.markdown
    manifest = _manifest(result.artifact)
    security_slot = next(
        row for row in manifest["slot_coverage_summary"] if row["slot_id"] == "deployment_security"
    )
    assert security_slot["status"] == "missing"


def test_deployment_report_uses_full_evidence_excerpt_over_truncated_citation(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="How to deploy SearXNG with Docker?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://raw.githubusercontent.com/searxng/searxng/master/container/docker-compose.yml",
            domain="raw.githubusercontent.com",
            title="docker-compose.yml",
            source_type="plain_text",
            published_at=None,
            fetched_at=datetime(2026, 5, 5, 10, 0, tzinfo=UTC),
            authority_score=0.95,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.95,
        )
    )
    full_compose = (
        "services:\n"
        "  searxng:\n"
        "    image: docker.io/searxng/searxng:latest\n"
        "    environment:\n"
        "      - SEARXNG_SECRET=change-me\n"
        "      - SEARXNG_BASE_URL=https://example.test/\n"
        "    ports:\n"
        "      - 8888:8080\n"
    )
    truncated_excerpt = "services:\n  searxng:"
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=full_compose,
            token_count=42,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.9,
                "quality_reasons": ["deployment_code_or_config"],
            },
        )
    )
    statement = f"Deployment Compose evidence: `{full_compose}`."
    claim = Claim(
        task_id=task.id,
        statement=statement,
        claim_type="fact",
        confidence=0.92,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "deployment/self_hosting",
            "claim_quality_score": 0.9,
            "query_answer_score": 0.9,
            "slot_ids": [
                "deployment_run_or_compose",
                "deployment_ports",
                "deployment_configuration",
                "deployment_security",
            ],
            "evidence_kind": "deployment_code_or_config",
            "evidence_candidate": {"excerpt": full_compose},
        },
    )
    db_session.add(claim)
    db_session.flush()
    span = CitationSpan(
        source_chunk_id=source_chunk.id,
        start_offset=0,
        end_offset=len(truncated_excerpt),
        excerpt=truncated_excerpt,
        normalized_excerpt_hash="sha256:truncated-compose",
    )
    db_session.add(span)
    db_session.flush()
    db_session.add(
        ClaimEvidence(
            claim_id=claim.id,
            citation_span_id=span.id,
            relation_type="support",
            score=0.92,
        )
    )
    db_session.commit()
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    result = service.generate_markdown_report(task.id)

    assert "SEARXNG_SECRET=change-me" in result.markdown
    assert "SEARXNG_BASE_URL=https://example.test/" in result.markdown
    assert "8888:8080" in result.markdown
    assert "```" in result.markdown


def test_deployment_source_chunks_promote_to_supported_claims_and_chinese_report(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="How to deploy SearXNG with Docker?",
        constraints={"report_language": "zh-CN"},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://docs.searxng.org/admin/installation-docker",
            domain="docs.searxng.org",
            title="Installation container",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 5, 6, 10, 0, tzinfo=UTC),
            authority_score=0.95,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.95,
        )
    )
    docker_run = (
        "$ docker run --name searxng -d \\\n"
        "    -p 8888:8080 \\\n"
        '    -v "./config/:/etc/searxng/" \\\n'
        '    -v "./data/:/var/cache/searxng/" \\\n'
        "    docker.io/searxng/searxng:latest"
    )
    compose_commands = "cp .env.example .env\ndocker compose up -d\ndocker compose pull"
    source_text = (
        f"{docker_run}\n\n"
        f"{compose_commands}\n\n"
        "The host requires Docker or Podman, and operators can run sudo usermod -aG "
        "docker $USER when Docker group access is needed.\n\n"
        "Configure settings.yml, .env.example, .env, SEARXNG_SECRET, and SEARXNG_BASE_URL "
        "for the container deployment.\n\n"
        "Use a reverse proxy with certificates, update-ca-certificates, and limiter bot "
        "protection before exposing a public SearXNG instance.\n\n"
        "Use docker compose logs and docker compose exec searxng sh for troubleshooting."
    )
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=source_text,
            token_count=130,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.95,
                "eligible_for_claims": True,
                "quality_reasons": ["deployment_code_or_config"],
            },
        )
    )
    db_session.commit()
    claims_service = create_claim_drafting_service(
        db_session,
        index_backend=EmptyChunkIndexBackend(),
        max_candidates_per_request=8,
        verification_max_claims_per_request=8,
        retrieval_max_results_per_request=5,
    )

    draft_result = claims_service.draft_claims(
        task.id,
        query=task.query,
        source_chunk_ids=[source_chunk.id],
        limit=8,
    )
    verify_result = claims_service.verify_claims(task.id, claim_ids=None, limit=8)

    claims = list(db_session.scalars(select(Claim).where(Claim.task_id == task.id)))
    supported_claims = [claim for claim in claims if claim.verification_status == "supported"]
    supported_text = "\n".join(claim.statement for claim in supported_claims)

    assert draft_result.created_claims >= 6
    assert verify_result.verified_claims == len(draft_result.entries)
    assert len(supported_claims) == len(draft_result.entries)
    for expected in (
        "Docker or Podman",
        "sudo usermod -aG docker",
        "docker compose pull",
        "settings.yml",
        ".env.example",
        ".env",
        "SEARXNG_SECRET",
        "SEARXNG_BASE_URL",
        "reverse proxy",
        "limiter bot protection",
        "certificates",
        "update-ca-certificates",
        "docker compose logs",
        "docker compose exec searxng sh",
    ):
        assert expected in supported_text

    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    report_service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    result = report_service.generate_markdown_report(task.id)

    assert result.report_language == "zh-CN"
    assert result.writer_mode == "deterministic"
    assert "## 执行摘要" in result.markdown
    assert "### 前置条件" in result.markdown
    assert "### Docker run / Docker Compose" in result.markdown
    assert "### 配置" in result.markdown
    assert "### 安全" in result.markdown
    assert "### 故障排查" in result.markdown
    assert "### 更新 / 维护" in result.markdown
    assert "```" in result.markdown
    assert docker_run in result.markdown
    assert "claim_evidence" not in result.markdown
    for expected in (
        "Docker or Podman",
        "sudo usermod -aG docker",
        "docker compose pull",
        "settings.yml",
        ".env.example",
        ".env",
        "SEARXNG_SECRET",
        "SEARXNG_BASE_URL",
        "reverse proxy",
        "limiter bot protection",
        "certificates",
        "update-ca-certificates",
    ):
        assert expected in result.markdown

    support_evidence = [
        evidence
        for claim in supported_claims
        for evidence in claim.claim_evidences
        if evidence.relation_type == "support"
    ]
    assert support_evidence
    assert all(evidence.citation_span.excerpt in source_text for evidence in support_evidence)


def test_grounded_deployment_report_renders_all_supported_slot_claims(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="How to deploy SearXNG with Docker?",
        constraints={"report_language": "zh-CN"},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://docs.searxng.org/admin/installation-docker",
            domain="docs.searxng.org",
            title="Installation container",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 5, 6, 10, 0, tzinfo=UTC),
            authority_score=0.95,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.95,
        )
    )
    docker_run = (
        "$ docker run --name searxng -d \\\n"
        "    -p 8888:8080 \\\n"
        '    -v "./config/:/etc/searxng/" \\\n'
        '    -v "./data/:/var/cache/searxng/" \\\n'
        "    docker.io/searxng/searxng:latest"
    )
    source_text = (
        "The host requires Docker or Podman before deploying SearXNG with containers.\n\n"
        "Operators can run sudo usermod -aG docker $USER when Docker group access is needed.\n\n"
        f"{docker_run}\n\n"
        "Run docker compose pull when updating the container deployment.\n\n"
        "Configure settings.yml for the SearXNG container deployment.\n\n"
        "Copy .env.example to .env before starting Docker Compose.\n\n"
        "Set SEARXNG_BASE_URL=https://search.example.test/ for the deployment.\n\n"
        "Use a reverse proxy before exposing a public SearXNG instance.\n\n"
        "Enable limiter bot protection before exposing the public instance.\n\n"
        "Install custom certificates with update-ca-certificates for trusted outbound TLS.\n\n"
        "Run docker compose logs for troubleshooting the SearXNG container.\n\n"
        "Run docker compose exec searxng sh for troubleshooting shell access."
    )
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=source_text,
            token_count=170,
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.95,
                "eligible_for_claims": True,
                "quality_reasons": ["deployment_code_or_config"],
            },
        )
    )
    db_session.commit()
    claims_service = create_claim_drafting_service(
        db_session,
        index_backend=EmptyChunkIndexBackend(),
        max_candidates_per_request=12,
        verification_max_claims_per_request=12,
        retrieval_max_results_per_request=5,
    )

    draft_result = claims_service.draft_claims(
        task.id,
        query=task.query,
        source_chunk_ids=[source_chunk.id],
        limit=12,
    )
    claims_service.verify_claims(task.id, claim_ids=None, limit=12)

    claims = list(db_session.scalars(select(Claim).where(Claim.task_id == task.id)))
    supported_claims = [claim for claim in claims if claim.verification_status == "supported"]
    assert len(draft_result.entries) > 8
    assert len(supported_claims) == len(draft_result.entries)
    first_claim = supported_claims[0]
    first_support = next(
        evidence for evidence in first_claim.claim_evidences if evidence.relation_type == "support"
    )
    provider = FakeReportLLMProvider(
        {
            "title": "SearXNG Docker 部署报告",
            "executive_summary": [
                {
                    "text": "部署结论只使用已验证证据。",
                    "claim_ids": [str(first_claim.id)],
                    "claim_evidence_ids": [str(first_support.id)],
                    "citation_span_ids": [str(first_support.citation_span_id)],
                }
            ],
            "sections": [],
            "uncertainties": [],
            "unresolved": [],
        }
    )
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    report_service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
        llm_provider=provider,
        llm_model="fake-report-model",
        llm_report_writer_enabled=True,
    )

    result = report_service.generate_markdown_report(task.id)

    assert result.report_language == "zh-CN"
    assert result.writer_mode == "llm_grounded"
    assert docker_run in result.markdown
    assert "Docker or Podman" in result.markdown
    assert "sudo usermod -aG docker" in result.markdown
    assert "docker compose pull" in result.markdown
    assert "settings.yml" in result.markdown
    assert ".env.example" in result.markdown
    assert ".env" in result.markdown
    assert "SEARXNG_BASE_URL" in result.markdown
    assert "reverse proxy" in result.markdown
    assert "limiter bot protection" in result.markdown
    assert "update-ca-certificates" in result.markdown
    assert "docker compose logs" in result.markdown
    assert "docker compose exec searxng sh" in result.markdown
    assert str(first_claim.id) not in result.markdown
    assert result.markdown.count("claim_evidence") <= 1


def test_deployment_report_includes_official_repository_archived_caveat(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="How to deploy SearXNG with Docker?",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://github.com/searxng/searxng-docker",
            domain="github.com",
            title="searxng/searxng-docker",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 5, 5, 10, 0, tzinfo=UTC),
            authority_score=0.86,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.86,
        )
    )
    statement = (
        "The searxng-docker repository is archived and superseded by the main SearXNG repository."
    )
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=statement,
            token_count=18,
            metadata_json={"eligible_for_claims": True, "content_quality_score": 0.9},
        )
    )
    claim = Claim(
        task_id=task.id,
        statement=statement,
        claim_type="fact",
        confidence=0.9,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "deployment/self_hosting",
            "claim_quality_score": 0.9,
            "query_answer_score": 0.9,
            "slot_ids": ["deployment_update_maintenance"],
        },
    )
    db_session.add(claim)
    db_session.flush()
    span = CitationSpan(
        source_chunk_id=source_chunk.id,
        start_offset=0,
        end_offset=len(statement),
        excerpt=statement,
        normalized_excerpt_hash="sha256:archived-caveat",
    )
    db_session.add(span)
    db_session.flush()
    db_session.add(
        ClaimEvidence(
            claim_id=claim.id,
            citation_span_id=span.id,
            relation_type="support",
            score=0.9,
        )
    )
    db_session.commit()
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    service = create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )

    result = service.generate_markdown_report(task.id)
    manifest = _manifest(result.artifact)
    update_slot = next(
        row
        for row in manifest["slot_coverage_summary"]
        if row["slot_id"] == "deployment_update_maintenance"
    )

    assert "### Update / maintenance" in result.markdown
    assert "archived and superseded" in result.markdown
    assert update_slot["status"] == "covered"


class SeededReportClaims:
    def __init__(self, task_id: UUID) -> None:
        self.task_id = task_id


def _report_service(
    db_session: Session,
    tmp_path: Path,
) -> object:
    object_store = FilesystemSnapshotObjectStore(root_directory=str(tmp_path / "objects"))
    object_store.validate_configuration()
    return create_report_synthesis_service(
        db_session,
        object_store=object_store,
        report_storage_bucket="reports",
    )


def _add_supported_report_claim(
    db_session: Session,
    task: ResearchTask,
    *,
    statement: str,
    canonical_url: str,
    domain: str,
    notes: dict[str, object],
    verification_status: str = "supported",
    evidence_relation_type: str = "support",
) -> Claim:
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url=canonical_url,
            domain=domain,
            title="Report eligibility source",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 26, 10, 0, tzinfo=UTC),
            authority_score=0.8,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=0.8,
        )
    )
    source_chunk = SourceChunkRepository(db_session).add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text=statement,
            token_count=max(8, len(statement.split())),
            metadata_json={
                "strategy": "paragraph_window_v1",
                "content_quality_score": 0.9,
                "eligible_for_claims": True,
            },
        )
    )
    claim = Claim(
        task_id=task.id,
        statement=statement,
        claim_type="fact",
        confidence=0.9,
        verification_status=verification_status,
        notes_json=notes,
    )
    db_session.add(claim)
    db_session.flush()
    span = CitationSpan(
        source_chunk_id=source_chunk.id,
        start_offset=0,
        end_offset=len(statement),
        excerpt=statement,
        normalized_excerpt_hash=f"sha256:{sha256(statement.encode()).hexdigest()}",
    )
    db_session.add(span)
    db_session.flush()
    db_session.add(
        ClaimEvidence(
            claim_id=claim.id,
            citation_span_id=span.id,
            relation_type=evidence_relation_type,
            score=0.9,
        )
    )
    return claim


def test_supported_verified_claim_without_slot_metadata_remains_reportable(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is ExampleProject?",
        constraints={},
    )
    statement = "ExampleProject is a workflow tool that coordinates verified research tasks."
    claim = _add_supported_report_claim(
        db_session,
        task,
        statement=statement,
        canonical_url="https://example.com/legacy-source",
        domain="example.com",
        notes={
            "verification": {"rationale": "Persisted support evidence exists."},
            "claim_category": "legacy_fact",
            "answer_role": "definition",
            "answer_relevant": True,
            "claim_quality_score": 0.9,
            "query_answer_score": 0.9,
        },
    )
    db_session.commit()
    service = _report_service(db_session, tmp_path)

    result = service.generate_markdown_report(task.id)
    db_session.refresh(claim)
    eligibility = claim.notes_json["report_eligibility"]

    assert result.supported_claims == 1
    assert statement in result.markdown
    assert claim.notes_json["report_eligible"] is True
    assert "missing_answer_slot" not in eligibility["reasons"]


def test_report_eligible_filter_keeps_generic_contradicted_ledger_claims(
    db_session: Session,
    tmp_path: Path,
) -> None:
    task = create_research_task_service(db_session).create_task(
        query="What is ExampleProject?",
        constraints={},
    )
    statement = "ExampleProject is a synchronous-only research task runner."
    claim = _add_supported_report_claim(
        db_session,
        task,
        statement=statement,
        canonical_url="https://example.com/generic-source",
        domain="example.com",
        notes={
            "verification": {"rationale": "Persisted contradictory evidence exists."},
            "claim_category": "definition",
            "answer_role": "definition",
            "answer_relevant": True,
            "claim_quality_score": 0.9,
            "query_answer_score": 0.9,
        },
        verification_status="contradicted",
        evidence_relation_type="contradict",
    )
    db_session.commit()
    service = _report_service(db_session, tmp_path)

    result = service.generate_markdown_report(task.id)
    db_session.refresh(claim)

    assert result.contradicted_claims == 1
    assert statement in result.markdown
    assert claim.notes_json["report_eligible"] is True


def _first_supported_claim_and_evidence(
    db_session: Session,
    task_id: UUID,
) -> tuple[Claim, ClaimEvidence]:
    claim = db_session.scalars(
        select(Claim)
        .where(Claim.task_id == task_id)
        .where(Claim.verification_status == "supported")
    ).one()
    evidence = db_session.scalars(
        select(ClaimEvidence)
        .where(ClaimEvidence.claim_id == claim.id)
        .where(ClaimEvidence.relation_type == "support")
    ).one()
    return claim, evidence


def _seed_verified_claims(db_session: Session) -> SeededReportClaims:
    task = create_research_task_service(db_session).create_task(
        query="explain verified research positions.",
        constraints={},
    )
    source_document = SourceDocumentRepository(db_session).add(
        SourceDocument(
            task_id=task.id,
            content_snapshot_id=None,
            canonical_url="https://example.com/report-source",
            domain="example.com",
            title="Example report source",
            source_type="web_page",
            published_at=None,
            fetched_at=datetime(2026, 4, 24, 10, 0, tzinfo=UTC),
            authority_score=None,
            freshness_score=None,
            originality_score=None,
            consistency_score=None,
            safety_score=None,
            final_source_score=None,
        )
    )
    source_chunk_repo = SourceChunkRepository(db_session)
    support_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=0,
            text="This supported claim is backed by the source document.",
            token_count=11,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    contradict_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=1,
            text="This mixed claim is not fully supported by the source document.",
            token_count=12,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    mixed_support_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=2,
            text="The mixed claim remains under dispute according to this source.",
            token_count=10,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )
    unsupported_chunk = source_chunk_repo.add(
        SourceChunk(
            source_document_id=source_document.id,
            chunk_no=3,
            text="This unsupported claim is contradicted by the source document.",
            token_count=11,
            metadata_json={"strategy": "paragraph_window_v1"},
        )
    )

    supported_claim = Claim(
        task_id=task.id,
        statement="This supported claim is backed by the source document.",
        claim_type="fact",
        confidence=0.92,
        verification_status="supported",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and no contradict evidence."},
            "claim_category": "definition",
            "claim_quality_score": 0.9,
            "query_answer_score": 0.9,
            "slot_ids": ["definition"],
        },
    )
    mixed_claim = Claim(
        task_id=task.id,
        statement="The mixed claim remains under dispute.",
        claim_type="fact",
        confidence=0.68,
        verification_status="mixed",
        notes_json={
            "verification": {"rationale": "Found 1 support evidence and 1 contradict evidence."},
            "claim_category": "mechanism",
            "claim_quality_score": 0.82,
            "query_answer_score": 0.8,
            "slot_ids": ["mechanism"],
        },
    )
    unsupported_claim = Claim(
        task_id=task.id,
        statement="The unsupported claim currently lacks support evidence.",
        claim_type="fact",
        confidence=0.41,
        verification_status="unsupported",
        notes_json={
            "verification": {
                "rationale": "No support evidence found; found 1 contradict evidence."
            },
            "claim_category": "privacy",
            "claim_quality_score": 0.78,
            "query_answer_score": 0.75,
            "slot_ids": ["limitations"],
        },
    )
    db_session.add_all([supported_claim, mixed_claim, unsupported_claim])
    db_session.flush()

    support_span = CitationSpan(
        source_chunk_id=support_chunk.id,
        start_offset=0,
        end_offset=len(support_chunk.text),
        excerpt=support_chunk.text,
        normalized_excerpt_hash="sha256:support",
    )
    mixed_support_span = CitationSpan(
        source_chunk_id=mixed_support_chunk.id,
        start_offset=0,
        end_offset=len(mixed_support_chunk.text),
        excerpt=mixed_support_chunk.text,
        normalized_excerpt_hash="sha256:mixed-support",
    )
    mixed_contradict_span = CitationSpan(
        source_chunk_id=contradict_chunk.id,
        start_offset=0,
        end_offset=len(contradict_chunk.text),
        excerpt=contradict_chunk.text,
        normalized_excerpt_hash="sha256:mixed-contradict",
    )
    unsupported_contradict_span = CitationSpan(
        source_chunk_id=unsupported_chunk.id,
        start_offset=0,
        end_offset=len(unsupported_chunk.text),
        excerpt=unsupported_chunk.text,
        normalized_excerpt_hash="sha256:unsupported-contradict",
    )
    db_session.add_all(
        [
            support_span,
            mixed_support_span,
            mixed_contradict_span,
            unsupported_contradict_span,
        ]
    )
    db_session.flush()

    db_session.add_all(
        [
            ClaimEvidence(
                claim_id=supported_claim.id,
                citation_span_id=support_span.id,
                relation_type="support",
                score=0.92,
            ),
            ClaimEvidence(
                claim_id=mixed_claim.id,
                citation_span_id=mixed_support_span.id,
                relation_type="support",
                score=0.66,
            ),
            ClaimEvidence(
                claim_id=mixed_claim.id,
                citation_span_id=mixed_contradict_span.id,
                relation_type="contradict",
                score=0.81,
            ),
            ClaimEvidence(
                claim_id=unsupported_claim.id,
                citation_span_id=unsupported_contradict_span.id,
                relation_type="contradict",
                score=0.79,
            ),
        ]
    )
    db_session.commit()
    return SeededReportClaims(task.id)


def _markdown_hash(markdown: str) -> str:
    return f"sha256:{sha256(markdown.encode('utf-8')).hexdigest()}"
