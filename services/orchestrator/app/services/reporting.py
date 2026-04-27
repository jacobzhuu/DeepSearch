from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast
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
    is_claimable_excerpt,
    is_claimable_statement,
    score_claim_statement,
)
from services.orchestrator.app.reporting import (
    ClaimStatus,
    EvidenceRelation,
    RenderedMarkdownReport,
    ReportClaimItem,
    ReportEvidenceItem,
    ReportSourceItem,
    build_report_manifest,
    compute_report_content_hash,
    extract_report_title,
    render_markdown_report,
)
from services.orchestrator.app.services.research_tasks import TaskNotFoundError
from services.orchestrator.app.storage import SnapshotObjectStore

REPORT_FORMAT_MARKDOWN = "markdown"
logger = get_logger(__name__)


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
    supported_claims: int
    mixed_claims: int
    unsupported_claims: int
    draft_claims: int


@dataclass(frozen=True)
class PreparedReport:
    rendered: RenderedMarkdownReport
    claims: list[ReportClaimItem]
    sources: list[ReportSourceItem]


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
    ) -> None:
        self.session = session
        self.task_repository = task_repository
        self.claim_repository = claim_repository
        self.claim_evidence_repository = claim_evidence_repository
        self.report_artifact_repository = report_artifact_repository
        self.object_store = object_store
        self.report_storage_bucket = report_storage_bucket

    def generate_markdown_report(self, task_id: UUID) -> ReportSynthesisResult:
        task = self._get_task(task_id)
        prepared_report = self._prepare_report(task)
        rendered = prepared_report.rendered
        content_hash = compute_report_content_hash(rendered.markdown)
        markdown_bytes = rendered.markdown.encode("utf-8")
        manifest = build_report_manifest(
            task_id=task.id,
            revision_no=task.revision_no,
            query=task.query,
            report_title=rendered.title,
            claims=prepared_report.claims,
            sources=prepared_report.sources,
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
        return ReportSynthesisResult(
            task=task,
            artifact=artifact,
            title=extract_report_title(markdown),
            markdown=markdown,
            reused_existing=True,
            supported_claims=0,
            mixed_claims=0,
            unsupported_claims=0,
            draft_claims=0,
        )

    def _prepare_report(self, task: ResearchTask) -> PreparedReport:
        claims = self.claim_repository.list_for_task(task.id)
        claim_evidence = self.claim_evidence_repository.list_for_task(task.id)
        evidence_by_claim_id: dict[UUID, list[ClaimEvidence]] = {claim.id: [] for claim in claims}
        for evidence in claim_evidence:
            evidence_by_claim_id.setdefault(evidence.claim_id, []).append(evidence)

        report_claims: list[ReportClaimItem] = []
        source_items: dict[UUID, ReportSourceItem] = {}
        excluded_low_quality_claim_count = 0
        for claim in claims:
            if not is_claimable_statement(claim.statement, query=task.query):
                excluded_low_quality_claim_count += 1
                continue
            claim_score = _report_claim_score(claim, query=task.query)
            if not _report_claim_answer_relevant(claim_score, query=task.query):
                excluded_low_quality_claim_count += 1
                continue
            support_evidence: list[ReportEvidenceItem] = []
            contradict_evidence: list[ReportEvidenceItem] = []
            for evidence in evidence_by_claim_id.get(claim.id, []):
                citation_span = evidence.citation_span
                if not is_claimable_excerpt(citation_span.excerpt):
                    continue
                source_chunk = citation_span.source_chunk
                if not _source_chunk_eligible_for_report(source_chunk):
                    continue
                source_document = source_chunk.source_document
                report_evidence = ReportEvidenceItem(
                    claim_evidence_id=evidence.id,
                    citation_span_id=citation_span.id,
                    source_document_id=source_document.id,
                    source_chunk_id=source_chunk.id,
                    relation_type=cast(EvidenceRelation, evidence.relation_type),
                    score=evidence.score,
                    canonical_url=source_document.canonical_url,
                    domain=source_document.domain,
                    chunk_no=source_chunk.chunk_no,
                    start_offset=citation_span.start_offset,
                    end_offset=citation_span.end_offset,
                    excerpt=citation_span.excerpt,
                )
                if evidence.relation_type == "support":
                    support_evidence.append(report_evidence)
                    source_items[source_document.id] = ReportSourceItem(
                        source_document_id=source_document.id,
                        canonical_url=source_document.canonical_url,
                        domain=source_document.domain,
                        title=source_document.title,
                    )
                elif evidence.relation_type == "contradict":
                    contradict_evidence.append(report_evidence)
                    source_items[source_document.id] = ReportSourceItem(
                        source_document_id=source_document.id,
                        canonical_url=source_document.canonical_url,
                        domain=source_document.domain,
                        title=source_document.title,
                    )

            normalized_status = self._normalize_status(claim.verification_status)
            if normalized_status in {"supported", "mixed"} and not support_evidence:
                normalized_status = "unsupported"

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
                )
            )

        sources = list(source_items.values())
        return PreparedReport(
            rendered=render_markdown_report(
                task_id=task.id,
                research_question=task.query,
                revision_no=task.revision_no,
                claims=report_claims,
                sources=sources,
                answer_relevant_claim_count=len(report_claims),
                excluded_low_quality_claim_count=excluded_low_quality_claim_count,
            ),
            claims=report_claims,
            sources=sources,
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
        reused_existing: bool,
    ) -> ReportSynthesisResult:
        return ReportSynthesisResult(
            task=task,
            artifact=artifact,
            title=rendered.title,
            markdown=rendered.markdown,
            reused_existing=reused_existing,
            supported_claims=rendered.supported_count,
            mixed_claims=rendered.mixed_count,
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
        verification_notes = claim.notes_json.get("verification", {})
        rationale = verification_notes.get("rationale")
        if isinstance(rationale, str) and rationale.strip():
            return rationale.strip()
        return None

    def _normalize_status(self, status: str) -> str:
        normalized_status = status.strip().lower()
        if normalized_status in {"draft", "supported", "mixed", "unsupported"}:
            return normalized_status
        return "draft"


def create_report_synthesis_service(
    session: Session,
    *,
    object_store: SnapshotObjectStore,
    report_storage_bucket: str,
) -> ReportSynthesisService:
    return ReportSynthesisService(
        session,
        task_repository=ResearchTaskRepository(session),
        claim_repository=ClaimRepository(session),
        claim_evidence_repository=ClaimEvidenceRepository(session),
        report_artifact_repository=ReportArtifactRepository(session),
        object_store=object_store,
        report_storage_bucket=report_storage_bucket,
    )


def _source_chunk_eligible_for_report(source_chunk: object) -> bool:
    metadata = getattr(source_chunk, "metadata_json", {}) or {}
    if metadata.get("eligible_for_claims") is False:
        return False
    if metadata.get("should_generate_claims") is False:
        return False
    if metadata.get("is_reference_section") is True:
        return False
    if metadata.get("is_navigation_noise") is True:
        return False
    if metadata.get("reason") == "redirect_stub":
        return False
    quality_score = metadata.get("content_quality_score")
    if isinstance(quality_score, int | float) and quality_score < 0.3:
        return False
    return True


def _report_claim_score(claim: Claim, *, query: str) -> ClaimCandidateScore:
    notes = claim.notes_json or {}
    noted_score = _claim_score_from_notes(notes)
    if noted_score is not None:
        return noted_score
    return score_claim_statement(statement=claim.statement, query=query)


def _report_claim_answer_relevant(score: ClaimCandidateScore, *, query: str) -> bool:
    if score.rejected_reason is not None:
        return False
    if score.claim_quality_score < REPORT_CLAIM_QUALITY_THRESHOLD:
        return False
    if score.query_answer_score < REPORT_QUERY_ANSWER_THRESHOLD:
        return False

    intent = classify_query_intent(query)
    if intent.intent_name == "generic":
        return score.claim_category not in intent.avoid_claim_types
    if score.claim_category in intent.expected_claim_types:
        return True
    if score.claim_category in intent.avoid_claim_types:
        return False
    return score.query_answer_score >= 0.85 and score.claim_quality_score >= 0.7


def _claim_score_from_notes(notes: dict[str, object]) -> ClaimCandidateScore | None:
    claim_quality_score = _numeric_note(notes.get("claim_quality_score"))
    query_answer_score = _numeric_note(notes.get("query_answer_score"))
    if claim_quality_score is None or query_answer_score is None:
        return None

    claim_category = notes.get("claim_category")
    rejected_reason = notes.get("rejected_reason")
    return ClaimCandidateScore(
        claim_category=claim_category if isinstance(claim_category, str) else "other",
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
