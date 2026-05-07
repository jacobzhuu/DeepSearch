from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, cast
from uuid import UUID

from sqlalchemy.orm import Session

from packages.db.models import Claim, ClaimEvidence, ReportArtifact, ResearchTask
from packages.db.repositories import (
    ClaimEvidenceRepository,
    ClaimRepository,
    ReportArtifactRepository,
    ResearchTaskRepository,
)
from packages.observability import get_logger, record_report_result
from services.orchestrator.app.claims import (
    REPORT_CLAIM_QUALITY_THRESHOLD,
    REPORT_QUERY_ANSWER_THRESHOLD,
    ClaimCandidateScore,
    classify_query_intent,
    is_answer_relevant_score,
    is_claimable_excerpt,
    is_claimable_statement,
    is_deployment_evidence_excerpt,
    is_deployment_evidence_statement,
    score_claim_statement,
)
from services.orchestrator.app.llm import LLMError, LLMProvider
from services.orchestrator.app.reporting import (
    DEFAULT_REPORT_LANGUAGE,
    ClaimStatus,
    EvidenceRelation,
    GroundedLLMReportValidationError,
    RenderedMarkdownReport,
    ReportClaimItem,
    ReportEvidenceItem,
    ReportSourceItem,
    build_report_manifest,
    compute_report_content_hash,
    extract_report_title,
    render_grounded_llm_report,
    render_markdown_report,
    resolve_report_language,
)
from services.orchestrator.app.research_quality import (
    build_slot_coverage_summary,
    contribution_level_for_counts,
    slot_ids_for_claim_category,
    summarize_evidence_yield,
)
from services.orchestrator.app.services.research_tasks import TaskNotFoundError
from services.orchestrator.app.storage import SnapshotObjectStore

REPORT_FORMAT_MARKDOWN = "markdown"
logger = get_logger(__name__)
_REPORT_REVIEW_EXCLUDE_DECISIONS = {
    "downrank",
    "reject",
    "duplicate",
    "vague",
    "split_needed",
}
_REPORT_ACCEPT_MIN_CONFIDENCE = 0.65
_EVENT_OR_ANNOUNCEMENT_MARKERS = {
    "conference",
    "conf ",
    "conf'",
    "webinar",
    "meetup",
    "summit",
    "happening on",
    "registration",
    "call for papers",
}
_EVENT_QUERY_MARKERS = {
    "conference",
    "event",
    "webinar",
    "meetup",
    "summit",
    "news",
    "latest",
    "recent",
    "announcement",
    "release",
}
_QUERY_FOCUS_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "compare",
    "comparison",
    "differences",
    "does",
    "do",
    "for",
    "how",
    "in",
    "is",
    "it",
    "of",
    "on",
    "or",
    "the",
    "to",
    "versus",
    "vs",
    "what",
    "with",
    "work",
    "works",
}


class ReportArtifactNotFoundError(Exception):
    def __init__(self, task_id: UUID) -> None:
        super().__init__(f"no markdown report artifact was found for task {task_id}")
        self.task_id = task_id


class ReportArtifactObjectMissingError(Exception):
    def __init__(self, task_id: UUID, artifact_id: UUID) -> None:
        super().__init__(
            f"report artifact object for task {task_id} and artifact {artifact_id} is missing"
        )
        self.task_id = task_id
        self.artifact_id = artifact_id


class ReportArtifactContentMismatchError(Exception):
    def __init__(self, task_id: UUID, artifact_id: UUID) -> None:
        super().__init__(
            "report artifact content for task"
            f" {task_id} and artifact {artifact_id} failed hash verification"
        )
        self.task_id = task_id
        self.artifact_id = artifact_id


@dataclass(frozen=True)
class ReportSynthesisResult:
    task: ResearchTask
    artifact: ReportArtifact
    title: str
    markdown: str
    reused_existing: bool
    report_language: str
    writer_mode: str
    llm_writer_status: str | None
    supported_claims: int
    mixed_claims: int
    contradicted_claims: int
    unsupported_claims: int
    draft_claims: int


@dataclass(frozen=True)
class PreparedReport:
    rendered: RenderedMarkdownReport
    claims: list[ReportClaimItem]
    sources: list[ReportSourceItem]
    report_language: str
    report_writer: dict[str, object]


class ReportSynthesisService:
    def __init__(
        self,
        session: Session,
        *,
        task_repository: ResearchTaskRepository,
        claim_repository: ClaimRepository,
        claim_evidence_repository: ClaimEvidenceRepository,
        report_artifact_repository: ReportArtifactRepository,
        object_store: SnapshotObjectStore,
        report_storage_bucket: str,
        llm_provider: LLMProvider | None = None,
        llm_model: str = "",
        llm_report_writer_enabled: bool = False,
        llm_report_max_output_tokens: int = 2400,
        include_ledger_debug_appendix: bool = False,
    ) -> None:
        self.session = session
        self.task_repository = task_repository
        self.claim_repository = claim_repository
        self.claim_evidence_repository = claim_evidence_repository
        self.report_artifact_repository = report_artifact_repository
        self.object_store = object_store
        self.report_storage_bucket = report_storage_bucket
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.llm_report_writer_enabled = llm_report_writer_enabled
        self.llm_report_max_output_tokens = llm_report_max_output_tokens
        self.include_ledger_debug_appendix = include_ledger_debug_appendix

    def generate_markdown_report(self, task_id: UUID) -> ReportSynthesisResult:
        task = self._get_task(task_id)
        prepared_report = self._prepare_report(task)
        rendered = prepared_report.rendered
        content_hash = compute_report_content_hash(rendered.markdown)
        markdown_bytes = rendered.markdown.encode("utf-8")
        report_diagnostics = _build_report_diagnostics(
            query=task.query,
            claims=prepared_report.claims,
            sources=prepared_report.sources,
        )
        manifest = build_report_manifest(
            task_id=task.id,
            revision_no=task.revision_no,
            query=task.query,
            report_title=rendered.title,
            report_language=prepared_report.report_language,
            report_writer=prepared_report.report_writer,
            claims=prepared_report.claims,
            sources=prepared_report.sources,
            slot_coverage_summary=cast(
                list[dict[str, Any]],
                report_diagnostics["slot_coverage_summary"],
            ),
            evidence_yield_summary=cast(
                dict[str, Any],
                report_diagnostics["evidence_yield_summary"],
            ),
            source_yield_summary=cast(
                list[dict[str, Any]],
                report_diagnostics["source_yield_summary"],
            ),
            verification_summary=cast(
                dict[str, Any],
                report_diagnostics["verification_summary"],
            ),
            dropped_sources=cast(
                list[dict[str, Any]],
                report_diagnostics["dropped_sources"],
            ),
        )
        latest_artifact = self.report_artifact_repository.get_latest_for_task_format(
            task.id,
            format=REPORT_FORMAT_MARKDOWN,
        )

        if latest_artifact is not None and self._artifact_matches(
            latest_artifact,
            content_hash=content_hash,
            content=markdown_bytes,
        ):
            record_report_result(reused_existing=True, format=REPORT_FORMAT_MARKDOWN)
            logger.info(
                "report.generated",
                extra={
                    "task_id": str(task.id),
                    "report_artifact_id": str(latest_artifact.id),
                    "version": latest_artifact.version,
                    "format": REPORT_FORMAT_MARKDOWN,
                    "reused_existing": True,
                },
            )
            return self._build_result(
                task=task,
                artifact=latest_artifact,
                rendered=rendered,
                report_language=prepared_report.report_language,
                report_writer=prepared_report.report_writer,
                reused_existing=True,
            )

        next_version = 1 if latest_artifact is None else latest_artifact.version + 1
        storage_key = self._build_storage_key(task.id, next_version)
        stored_ref = self.object_store.put_bytes(
            bucket=self.report_storage_bucket,
            key=storage_key,
            content=markdown_bytes,
            content_type="text/markdown; charset=utf-8",
        )
        artifact = self.report_artifact_repository.add(
            ReportArtifact(
                task_id=task.id,
                version=next_version,
                storage_bucket=stored_ref.bucket,
                storage_key=stored_ref.key,
                format=REPORT_FORMAT_MARKDOWN,
                content_hash=content_hash,
                manifest_json=manifest,
            )
        )
        self.session.commit()
        record_report_result(reused_existing=False, format=REPORT_FORMAT_MARKDOWN)
        logger.info(
            "report.generated",
            extra={
                "task_id": str(task.id),
                "report_artifact_id": str(artifact.id),
                "version": artifact.version,
                "format": REPORT_FORMAT_MARKDOWN,
                "reused_existing": False,
                "content_hash": content_hash,
            },
        )
        return self._build_result(
            task=task,
            artifact=artifact,
            rendered=rendered,
            report_language=prepared_report.report_language,
            report_writer=prepared_report.report_writer,
            reused_existing=False,
        )

    def get_latest_markdown_report(self, task_id: UUID) -> ReportSynthesisResult:
        task = self._get_task(task_id)
        artifact = self.report_artifact_repository.get_latest_for_task_format(
            task.id,
            format=REPORT_FORMAT_MARKDOWN,
        )
        if artifact is None:
            raise ReportArtifactNotFoundError(task.id)

        try:
            markdown_bytes = self.object_store.get_bytes(
                bucket=artifact.storage_bucket,
                key=artifact.storage_key,
            )
        except FileNotFoundError as error:
            raise ReportArtifactObjectMissingError(task.id, artifact.id) from error

        markdown = markdown_bytes.decode("utf-8")
        if (
            artifact.content_hash is not None
            and artifact.content_hash != compute_report_content_hash(markdown)
        ):
            raise ReportArtifactContentMismatchError(task.id, artifact.id)
        manifest = artifact.manifest_json if isinstance(artifact.manifest_json, dict) else {}
        writer = manifest.get("report_writer") if isinstance(manifest, dict) else {}
        writer = writer if isinstance(writer, dict) else {}
        return ReportSynthesisResult(
            task=task,
            artifact=artifact,
            title=extract_report_title(markdown),
            markdown=markdown,
            reused_existing=True,
            report_language=(
                _string_or_none(manifest.get("report_language")) or DEFAULT_REPORT_LANGUAGE
            ),
            writer_mode=_string_or_none(writer.get("mode")) or "unknown",
            llm_writer_status=_string_or_none(writer.get("status")),
            supported_claims=0,
            mixed_claims=0,
            contradicted_claims=0,
            unsupported_claims=0,
            draft_claims=0,
        )

    def _prepare_report(self, task: ResearchTask) -> PreparedReport:
        report_language = resolve_report_language(task.constraints_json)
        claims = self.claim_repository.list_for_task(task.id)
        claim_evidence = self.claim_evidence_repository.list_for_task(task.id)
        evidence_by_claim_id: dict[UUID, list[ClaimEvidence]] = {claim.id: [] for claim in claims}
        for evidence in claim_evidence:
            evidence_by_claim_id.setdefault(evidence.claim_id, []).append(evidence)

        report_claims: list[ReportClaimItem] = []
        source_items: dict[UUID, ReportSourceItem] = {}
        excluded_low_quality_claim_count = 0
        for claim in claims:
            notes = claim.notes_json or {}
            claim_score = _report_claim_score(claim, query=task.query)
            slot_ids = _report_claim_slot_ids(claim, claim_score=claim_score, query=task.query)
            review_decision = _claim_review_decision(notes)
            if not is_claimable_statement(claim.statement, query=task.query):
                _set_report_eligibility(
                    claim,
                    eligible=False,
                    reasons=["not_claimable_statement"],
                    slot_ids=slot_ids,
                    review_decision=review_decision,
                )
                excluded_low_quality_claim_count += 1
                continue
            if not _report_claim_answer_relevant(claim_score, query=task.query):
                _set_report_eligibility(
                    claim,
                    eligible=False,
                    reasons=["low_quality_or_off_query_score"],
                    slot_ids=slot_ids,
                    review_decision=review_decision,
                )
                excluded_low_quality_claim_count += 1
                continue
            support_evidence: list[ReportEvidenceItem] = []
            contradict_evidence: list[ReportEvidenceItem] = []
            relation_metadata_by_span = _relation_metadata_by_citation_span(claim)
            for evidence in evidence_by_claim_id.get(claim.id, []):
                citation_span = evidence.citation_span
                if not is_claimable_excerpt(
                    citation_span.excerpt
                ) and not _claim_uses_deployment_evidence(claim, citation_span.excerpt):
                    continue
                source_chunk = citation_span.source_chunk
                if not _source_chunk_eligible_for_report(source_chunk):
                    continue
                source_document = source_chunk.source_document
                relation_metadata = relation_metadata_by_span.get(str(citation_span.id), {})
                effective_relation_type = (
                    _string_or_none(relation_metadata.get("relation_type"))
                    or evidence.relation_type
                )
                if effective_relation_type != evidence.relation_type and relation_metadata:
                    continue
                report_evidence = ReportEvidenceItem(
                    claim_evidence_id=evidence.id,
                    citation_span_id=citation_span.id,
                    source_document_id=source_document.id,
                    source_chunk_id=source_chunk.id,
                    relation_type=cast(EvidenceRelation, effective_relation_type),
                    score=evidence.score,
                    canonical_url=source_document.canonical_url,
                    domain=source_document.domain,
                    chunk_no=source_chunk.chunk_no,
                    start_offset=citation_span.start_offset,
                    end_offset=citation_span.end_offset,
                    excerpt=citation_span.excerpt,
                    relation_detail=_string_or_none(relation_metadata.get("relation_detail")),
                    support_level=_string_or_none(relation_metadata.get("support_level")),
                    verifier_method=_string_or_none(relation_metadata.get("verifier_method")),
                    citation_precision=_string_or_none(relation_metadata.get("citation_precision")),
                    citation_precision_reason=_string_or_none(
                        relation_metadata.get("citation_precision_reason")
                    ),
                    reuse_penalty=_numeric_note(relation_metadata.get("reuse_penalty")),
                    chunk_reuse_count_before=_int_note(
                        relation_metadata.get("chunk_reuse_count_before")
                    ),
                    span_reuse_count_before=_int_note(
                        relation_metadata.get("span_reuse_count_before")
                    ),
                    content_reuse_count_before=_int_note(
                        relation_metadata.get("content_reuse_count_before")
                    ),
                    reasons=_relation_reasons(relation_metadata),
                )
                if effective_relation_type in {"support", "weak_support"}:
                    support_evidence.append(report_evidence)
                elif effective_relation_type == "contradict":
                    contradict_evidence.append(report_evidence)

            normalized_status = self._normalize_status(claim.verification_status)
            if normalized_status == "supported" and not support_evidence:
                normalized_status = "unsupported"
            if normalized_status == "mixed" and not (support_evidence and contradict_evidence):
                normalized_status = "unsupported" if not contradict_evidence else "contradicted"

            eligibility = _report_claim_eligibility(
                claim=claim,
                query=task.query,
                claim_score=claim_score,
                normalized_status=normalized_status,
                support_evidence=support_evidence,
                contradict_evidence=contradict_evidence,
                slot_ids=slot_ids,
                review_decision=review_decision,
            )
            _set_report_eligibility(
                claim,
                eligible=eligibility["eligible"],
                reasons=cast(list[str], eligibility["reasons"]),
                slot_ids=slot_ids,
                review_decision=review_decision,
            )
            if not eligibility["eligible"]:
                excluded_low_quality_claim_count += 1
                continue
            for evidence in support_evidence + contradict_evidence:
                source_items[evidence.source_document_id] = ReportSourceItem(
                    source_document_id=evidence.source_document_id,
                    canonical_url=evidence.canonical_url,
                    domain=evidence.domain,
                    title=None,
                )

            report_claims.append(
                ReportClaimItem(
                    claim_id=claim.id,
                    statement=claim.statement,
                    claim_type=claim.claim_type,
                    confidence=claim.confidence,
                    verification_status=cast(ClaimStatus, normalized_status),
                    rationale=self._extract_rationale(claim),
                    support_evidence=support_evidence,
                    contradict_evidence=contradict_evidence,
                    claim_quality_score=claim_score.claim_quality_score,
                    query_answer_score=claim_score.query_answer_score,
                    claim_category=claim_score.claim_category,
                    slot_ids=tuple(slot_ids),
                    evidence_kind=_string_or_none((claim.notes_json or {}).get("evidence_kind")),
                    deployment_evidence_excerpt=_deployment_evidence_excerpt_from_notes(
                        claim.notes_json or {}
                    ),
                    verifier_method=_claim_verifier_method(claim),
                    support_level=_claim_support_level(claim),
                )
            )

        sources = list(source_items.values())
        self.session.flush()
        deterministic_rendered = render_markdown_report(
            task_id=task.id,
            research_question=task.query,
            revision_no=task.revision_no,
            claims=report_claims,
            sources=sources,
            report_language=report_language,
            answer_relevant_claim_count=len(report_claims),
            excluded_low_quality_claim_count=excluded_low_quality_claim_count,
            include_ledger_debug_appendix=self.include_ledger_debug_appendix,
        )
        rendered = deterministic_rendered
        report_writer: dict[str, object] = {
            "mode": "deterministic",
            "status": "used",
            "language": report_language,
        }
        if self.llm_report_writer_enabled and self.llm_provider is not None:
            try:
                llm_report = render_grounded_llm_report(
                    task_id=task.id,
                    research_question=task.query,
                    revision_no=task.revision_no,
                    claims=report_claims,
                    sources=sources,
                    report_language=report_language,
                    answer_relevant_claim_count=len(report_claims),
                    excluded_low_quality_claim_count=excluded_low_quality_claim_count,
                    llm_provider=self.llm_provider,
                    llm_model=self.llm_model,
                    max_output_tokens=self.llm_report_max_output_tokens,
                    include_ledger_debug_appendix=self.include_ledger_debug_appendix,
                )
            except LLMError as error:
                report_writer = {
                    "mode": "deterministic",
                    "status": "fallback_after_llm_error",
                    "language": report_language,
                    "llm_error": error.to_payload(),
                }
                logger.warning(
                    "report.llm_writer_failed",
                    extra={
                        "task_id": str(task.id),
                        "error_code": error.error_code,
                        "provider": error.provider,
                    },
                )
            except GroundedLLMReportValidationError as error:
                report_writer = {
                    "mode": "deterministic",
                    "status": "fallback_after_llm_validation_error",
                    "language": report_language,
                    "validation_error": str(error),
                }
                logger.warning(
                    "report.llm_writer_invalid_output",
                    extra={
                        "task_id": str(task.id),
                        "validation_error": str(error),
                    },
                )
            else:
                rendered = llm_report.rendered
                report_writer = dict(llm_report.metadata)
                report_writer["language"] = report_language
        return PreparedReport(
            rendered=rendered,
            claims=report_claims,
            sources=sources,
            report_language=report_language,
            report_writer=report_writer,
        )

    def _artifact_matches(
        self,
        artifact: ReportArtifact,
        *,
        content_hash: str,
        content: bytes,
    ) -> bool:
        if artifact.content_hash is not None and artifact.content_hash != content_hash:
            return False
        try:
            latest_content = self.object_store.get_bytes(
                bucket=artifact.storage_bucket,
                key=artifact.storage_key,
            )
        except FileNotFoundError:
            return False
        return latest_content == content

    def _build_result(
        self,
        *,
        task: ResearchTask,
        artifact: ReportArtifact,
        rendered: RenderedMarkdownReport,
        report_language: str,
        report_writer: dict[str, object],
        reused_existing: bool,
    ) -> ReportSynthesisResult:
        return ReportSynthesisResult(
            task=task,
            artifact=artifact,
            title=rendered.title,
            markdown=rendered.markdown,
            reused_existing=reused_existing,
            report_language=report_language,
            writer_mode=_string_or_none(report_writer.get("mode")) or "unknown",
            llm_writer_status=_string_or_none(report_writer.get("status")),
            supported_claims=rendered.supported_count,
            mixed_claims=rendered.mixed_count,
            contradicted_claims=rendered.contradicted_count,
            unsupported_claims=rendered.unsupported_count,
            draft_claims=rendered.draft_count,
        )

    def _get_task(self, task_id: UUID) -> ResearchTask:
        task = self.task_repository.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def _build_storage_key(self, task_id: UUID, version: int) -> str:
        return str(PurePosixPath(str(task_id), f"v{version}", "report.md"))

    def _extract_rationale(self, claim: Claim) -> str | None:
        notes = claim.notes_json if isinstance(claim.notes_json, dict) else {}
        verification_notes = notes.get("verification", {})
        if not isinstance(verification_notes, dict):
            return None
        rationale = verification_notes.get("rationale")
        if isinstance(rationale, str) and rationale.strip():
            return rationale.strip()
        return None

    def _normalize_status(self, status: str) -> str:
        normalized_status = status.strip().lower()
        if normalized_status in {"draft", "supported", "mixed", "unsupported", "contradicted"}:
            return normalized_status
        return "draft"


def create_report_synthesis_service(
    session: Session,
    *,
    object_store: SnapshotObjectStore,
    report_storage_bucket: str,
    llm_provider: LLMProvider | None = None,
    llm_model: str = "",
    llm_report_writer_enabled: bool = False,
    llm_report_max_output_tokens: int = 2400,
    include_ledger_debug_appendix: bool = False,
) -> ReportSynthesisService:
    return ReportSynthesisService(
        session,
        task_repository=ResearchTaskRepository(session),
        claim_repository=ClaimRepository(session),
        claim_evidence_repository=ClaimEvidenceRepository(session),
        report_artifact_repository=ReportArtifactRepository(session),
        object_store=object_store,
        report_storage_bucket=report_storage_bucket,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_report_writer_enabled=llm_report_writer_enabled,
        llm_report_max_output_tokens=llm_report_max_output_tokens,
        include_ledger_debug_appendix=include_ledger_debug_appendix,
    )


def _source_chunk_eligible_for_report(source_chunk: object) -> bool:
    metadata = getattr(source_chunk, "metadata_json", {}) or {}
    quality_reasons = metadata.get("quality_reasons")
    deployment_code_or_config = (
        isinstance(quality_reasons, list) and "deployment_code_or_config" in quality_reasons
    )
    if metadata.get("eligible_for_claims") is False:
        return False
    if metadata.get("should_generate_claims") is False:
        return False
    if metadata.get("is_reference_section") is True:
        return False
    if metadata.get("is_navigation_noise") is True:
        return False
    if metadata.get("is_diagram_or_config_section") is True and not deployment_code_or_config:
        return False
    if metadata.get("reason") == "redirect_stub":
        return False
    quality_score = metadata.get("content_quality_score")
    if isinstance(quality_score, int | float) and quality_score < 0.3:
        return False
    return True


def _claim_uses_deployment_evidence(claim: Claim, excerpt: str) -> bool:
    notes = claim.notes_json or {}
    return (
        is_deployment_evidence_statement(claim.statement)
        or notes.get("evidence_kind") == "deployment_code_or_config"
    ) and is_deployment_evidence_excerpt(excerpt)


def _deployment_evidence_excerpt_from_notes(notes: dict[str, object]) -> str | None:
    evidence_candidate = notes.get("evidence_candidate")
    if not isinstance(evidence_candidate, dict):
        return None
    excerpt = evidence_candidate.get("excerpt")
    return excerpt if isinstance(excerpt, str) and excerpt.strip() else None


def _report_claim_score(claim: Claim, *, query: str) -> ClaimCandidateScore:
    notes = claim.notes_json or {}
    noted_score = _claim_score_from_notes(notes)
    if noted_score is not None:
        return noted_score
    return score_claim_statement(statement=claim.statement, query=query)


def _report_claim_answer_relevant(score: ClaimCandidateScore, *, query: str) -> bool:
    if score.claim_quality_score < REPORT_CLAIM_QUALITY_THRESHOLD:
        return False
    if score.query_answer_score < REPORT_QUERY_ANSWER_THRESHOLD:
        return False
    return is_answer_relevant_score(score, query=query)


def _report_claim_eligibility(
    *,
    claim: Claim,
    query: str,
    claim_score: ClaimCandidateScore,
    normalized_status: str,
    support_evidence: list[ReportEvidenceItem],
    contradict_evidence: list[ReportEvidenceItem],
    slot_ids: list[str],
    review_decision: dict[str, object],
) -> dict[str, object]:
    reasons: list[str] = []
    if normalized_status == "draft":
        reasons.append("not_verified")
    if not support_evidence and not contradict_evidence:
        reasons.append("missing_persisted_evidence")
    decision = _string_or_none(review_decision.get("decision"))
    if decision in _REPORT_REVIEW_EXCLUDE_DECISIONS:
        reasons.append(f"claim_review_{decision}")
    if decision == "accept":
        confidence = _numeric_note(review_decision.get("confidence"))
        review_reasons = review_decision.get("reasons")
        covered_slot_ids = review_decision.get("covered_slot_ids")
        quality_flags = review_decision.get("quality_flags")
        if confidence is None or confidence < _REPORT_ACCEPT_MIN_CONFIDENCE:
            reasons.append("claim_review_low_confidence_accept")
        if not isinstance(review_reasons, list) or not any(
            isinstance(item, str) and item.strip() for item in review_reasons
        ):
            reasons.append("claim_review_missing_reasons")
        if not isinstance(covered_slot_ids, list) or not any(
            isinstance(item, str) and item.strip() for item in covered_slot_ids
        ):
            reasons.append("claim_review_missing_slot_coverage")
        if isinstance(quality_flags, list) and quality_flags:
            reasons.append("claim_review_quality_flags")
    if _event_or_announcement_noise_claim(claim.statement, query=query):
        reasons.append("event_or_announcement_noise")
    if claim_score.rejected_reason:
        reasons.append(f"claim_score_rejected:{claim_score.rejected_reason}")
    if _claim_focus_required_for_report(query=query, slot_ids=slot_ids) and not (
        _claim_focus_matches_query(claim.statement, query=query)
    ):
        reasons.append("query_focus_mismatch")
    return {"eligible": not reasons, "reasons": list(dict.fromkeys(reasons))}


def _set_report_eligibility(
    claim: Claim,
    *,
    eligible: bool,
    reasons: list[str],
    slot_ids: list[str],
    review_decision: dict[str, object],
) -> None:
    notes = claim.notes_json or {}
    claim.notes_json = {
        **notes,
        "report_eligible": eligible,
        "report_eligibility": {
            "eligible": eligible,
            "reasons": reasons,
            "slot_ids": slot_ids,
            "review_decision": review_decision,
        },
    }


def _report_claim_slot_ids(
    claim: Claim,
    *,
    claim_score: ClaimCandidateScore,
    query: str,
) -> list[str]:
    notes = claim.notes_json or {}
    noted_slots = [
        item for item in notes.get("slot_ids", []) if isinstance(item, str) and item.strip()
    ]
    if noted_slots:
        return list(dict.fromkeys(noted_slots))
    return _slot_ids_from_claim_category(claim_score.claim_category, query=query)


def _claim_review_decision(notes: dict[str, object]) -> dict[str, object]:
    review = notes.get("llm_claim_review")
    return review if isinstance(review, dict) else {}


def _claim_focus_matches_query(statement: str, *, query: str) -> bool:
    required_terms = _query_required_focus_terms(query)
    if not required_terms:
        return True
    statement_tokens = set(_tokenize_focus(statement))
    statement_lower = statement.lower()
    return any(
        term in statement_tokens or term.replace("-", " ") in statement_lower
        for term in required_terms
    )


def _claim_focus_required_for_report(*, query: str, slot_ids: list[str]) -> bool:
    intent = classify_query_intent(query)
    if intent.intent_name == "deployment" and any(
        slot_id.startswith("deployment_") for slot_id in slot_ids
    ):
        return False
    return True


def _event_or_announcement_noise_claim(statement: str, *, query: str) -> bool:
    query_lower = query.lower()
    if any(marker in query_lower for marker in _EVENT_QUERY_MARKERS):
        return False
    statement_lower = statement.lower()
    return any(marker in statement_lower for marker in _EVENT_OR_ANNOUNCEMENT_MARKERS)


def _query_required_focus_terms(query: str) -> set[str]:
    terms: list[str] = []
    words = re.findall(r"[A-Za-z0-9_.-]+", query)
    lowered = [word.lower() for word in words]
    for index, token in enumerate(lowered):
        if token in {"compare", "comparison"}:
            terms.extend(_following_focus_terms(words[index + 1 :], stop_at={"for", "in"}))
            break
        if token in {"what"} and index + 1 < len(lowered) and lowered[index + 1] in {"is", "are"}:
            terms.extend(
                _following_focus_terms(
                    words[index + 2 :], stop_at={"and", "for", "in", "of", "with"}
                )
            )
        if token == "how" and index + 2 < len(lowered) and lowered[index + 1] in {"does", "do"}:
            terms.extend(_following_focus_terms(words[index + 2 :], stop_at={"work", "works"}))
    terms.extend(
        word.lower()
        for word in words
        if (
            (word[:1].isupper() or any(char.isdigit() for char in word))
            and word.lower() not in _QUERY_FOCUS_STOPWORDS
        )
    )
    return {
        term
        for term in dict.fromkeys(terms)
        if len(term) >= 3 and term not in _QUERY_FOCUS_STOPWORDS
    }


def _following_focus_terms(words: list[str], *, stop_at: set[str]) -> list[str]:
    terms: list[str] = []
    for word in words[:6]:
        lowered = word.lower()
        if lowered in stop_at:
            break
        if lowered in _QUERY_FOCUS_STOPWORDS:
            continue
        terms.append(lowered)
        if word[:1].isupper() or any(char.isdigit() for char in word):
            continue
        if len(terms) >= 1:
            break
    return terms


def _tokenize_focus(value: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9_.-]+", value)]


def _claim_score_from_notes(notes: dict[str, object]) -> ClaimCandidateScore | None:
    claim_quality_score = _numeric_note(notes.get("claim_quality_score"))
    query_answer_score = _numeric_note(notes.get("query_answer_score"))
    if claim_quality_score is None or query_answer_score is None:
        return None

    claim_category = notes.get("claim_category")
    rejected_reason = notes.get("rejected_reason")
    answer_role = notes.get("answer_role")
    answer_relevant = notes.get("answer_relevant")
    return ClaimCandidateScore(
        claim_category=claim_category if isinstance(claim_category, str) else "other",
        answer_role=answer_role if isinstance(answer_role, str) else "non_answer",
        answer_relevant=answer_relevant if isinstance(answer_relevant, bool) else False,
        content_quality_score=_numeric_note(notes.get("content_quality_score")) or 0.6,
        query_relevance_score=_numeric_note(notes.get("query_relevance_score")) or 0.0,
        claim_quality_score=claim_quality_score,
        query_answer_score=query_answer_score,
        source_quality_score=_numeric_note(notes.get("source_quality_score")) or 0.5,
        final_score=_numeric_note(notes.get("claim_selection_score")) or 0.0,
        rejected_reason=rejected_reason if isinstance(rejected_reason, str) else None,
    )


def _numeric_note(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _int_note(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _relation_metadata_by_citation_span(claim: Claim) -> dict[str, dict[str, object]]:
    notes = claim.notes_json or {}
    verification = notes.get("verification") if isinstance(notes, dict) else {}
    if not isinstance(verification, dict):
        return {}
    relation_rows = verification.get("evidence_relations")
    if not isinstance(relation_rows, list):
        return {}
    result: dict[str, dict[str, object]] = {}
    for row in relation_rows:
        if not isinstance(row, dict):
            continue
        citation_span_id = row.get("citation_span_id")
        if isinstance(citation_span_id, str) and citation_span_id.strip():
            result[citation_span_id] = row
    return result


def _relation_reasons(relation_metadata: dict[str, object]) -> tuple[str, ...]:
    reasons = relation_metadata.get("reasons")
    if not isinstance(reasons, list):
        return ()
    return tuple(item for item in reasons if isinstance(item, str))


def _claim_verifier_method(claim: Claim) -> str | None:
    notes = claim.notes_json or {}
    verification = notes.get("verification") if isinstance(notes, dict) else {}
    if not isinstance(verification, dict):
        return None
    return _string_or_none(verification.get("verifier_method") or verification.get("method"))


def _claim_support_level(claim: Claim) -> str:
    notes = claim.notes_json or {}
    verification = notes.get("verification") if isinstance(notes, dict) else {}
    if not isinstance(verification, dict):
        return "strong"
    strong = verification.get("strong_support_evidence_count")
    weak = verification.get("weak_support_evidence_count")
    if isinstance(weak, int | float) and weak > 0 and not strong:
        return "weak"
    return "strong"


def _build_report_diagnostics(
    *,
    query: str,
    claims: list[ReportClaimItem],
    sources: list[ReportSourceItem],
) -> dict[str, object]:
    evidence_candidates = [
        _evidence_candidate_from_claim(claim, query=query)
        for claim in claims
        if _evidence_candidate_from_claim(claim, query=query) is not None
    ]
    evidence_candidate_rows = [item for item in evidence_candidates if item is not None]
    claim_rows = [
        {
            "claim_id": str(claim.claim_id),
            "verification_status": claim.verification_status,
            "slot_ids": list(claim.slot_ids)
            or _slot_ids_from_claim_category(claim.claim_category, query=query),
            "source_document_id": (
                str(claim.support_evidence[0].source_document_id)
                if claim.support_evidence
                else None
            ),
            "support_level": claim.support_level or "strong",
        }
        for claim in claims
    ]
    accepted_candidate_ids: set[str] = set()
    for item in evidence_candidate_rows:
        evidence_candidate_id = item.get("evidence_candidate_id")
        if isinstance(evidence_candidate_id, str):
            accepted_candidate_ids.add(evidence_candidate_id)
    source_yield_summary = _report_source_yield_summary(
        sources=sources,
        claims=claims,
        evidence_candidates=evidence_candidate_rows,
    )
    return {
        "slot_coverage_summary": build_slot_coverage_summary(
            query,
            evidence_candidates=evidence_candidate_rows,
            claim_rows=claim_rows,
        ),
        "evidence_yield_summary": summarize_evidence_yield(
            evidence_candidate_rows,
            accepted_candidate_ids=accepted_candidate_ids,
            query=query,
        ),
        "source_yield_summary": source_yield_summary,
        "verification_summary": _report_verification_summary(claims),
        "dropped_sources": [
            row
            for row in source_yield_summary
            if row.get("contribution_level") == "none" and row.get("dropped_reasons")
        ],
    }


def _evidence_candidate_from_claim(
    claim: ReportClaimItem,
    *,
    query: str,
) -> dict[str, object] | None:
    if not claim.support_evidence:
        return None
    evidence = claim.support_evidence[0]
    return {
        "evidence_candidate_id": f"claim_{claim.claim_id}",
        "source_document_id": str(evidence.source_document_id),
        "source_chunk_id": str(evidence.source_chunk_id),
        "citation_span_id": str(evidence.citation_span_id),
        "slot_ids": list(claim.slot_ids)
        or _slot_ids_from_claim_category(claim.claim_category, query=query),
        "source_intent": "report_evidence_source",
        "excerpt": evidence.excerpt,
        "start_offset": evidence.start_offset,
        "end_offset": evidence.end_offset,
        "salience_score": claim.query_answer_score or 0.0,
        "quality_score": claim.claim_quality_score or 0.0,
        "extraction_strategy": None,
        "rejection_reasons": [],
        "metadata": {
            "claim_id": str(claim.claim_id),
            "verification_status": claim.verification_status,
            "support_level": claim.support_level,
            "evidence_kind": claim.evidence_kind,
        },
    }


def _report_source_yield_summary(
    *,
    sources: list[ReportSourceItem],
    claims: list[ReportClaimItem],
    evidence_candidates: list[dict[str, object]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for source in sources:
        source_id = str(source.source_document_id)
        source_claims = [
            claim
            for claim in claims
            if any(str(item.source_document_id) == source_id for item in claim.support_evidence)
        ]
        candidate_count = sum(
            1 for item in evidence_candidates if item.get("source_document_id") == source_id
        )
        accepted_evidence_count = sum(len(claim.support_evidence) for claim in source_claims)
        contribution_level = contribution_level_for_counts(
            accepted_evidence_count=accepted_evidence_count,
            claim_count=len(source_claims),
            candidate_count=candidate_count,
        )
        rows.append(
            {
                "source_document_id": source_id,
                "url": source.canonical_url,
                "canonical_url": source.canonical_url,
                "domain": source.domain,
                "title": source.title,
                "source_intent": "report_evidence_source",
                "attempted": True,
                "fetched": True,
                "parsed": True,
                "indexed": True,
                "candidate_count": candidate_count,
                "accepted_evidence_count": accepted_evidence_count,
                "claim_count": len(source_claims),
                "rejected_count": 0,
                "dropped_reasons": [],
                "contribution_level": contribution_level,
            }
        )
    return rows


def _report_verification_summary(claims: list[ReportClaimItem]) -> dict[str, object]:
    methods = sorted({claim.verifier_method for claim in claims if claim.verifier_method})
    return {
        "verifier_methods": methods,
        "strong_supported_claim_count": sum(
            1
            for claim in claims
            if claim.verification_status == "supported" and claim.support_level != "weak"
        ),
        "weak_supported_claim_count": sum(
            1
            for claim in claims
            if claim.verification_status == "supported" and claim.support_level == "weak"
        ),
        "mixed_claim_count": sum(1 for claim in claims if claim.verification_status == "mixed"),
        "contradicted_claim_count": sum(
            1 for claim in claims if claim.verification_status == "contradicted"
        ),
        "unsupported_claim_count": sum(
            1 for claim in claims if claim.verification_status == "unsupported"
        ),
        "limitations": [
            "report uses persisted verifier metadata",
            "weak support is not treated as a main-answer fact",
        ],
    }


def _slot_ids_from_claim_category(category: str | None, *, query: str) -> list[str]:
    if not category:
        return []
    return slot_ids_for_claim_category(category, query=query)


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
