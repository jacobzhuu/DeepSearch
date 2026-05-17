from __future__ import annotations

import json
import re
from collections.abc import Callable
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
    TaskEventRepository,
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
from services.orchestrator.app.llm.providers import NoopLLMProvider
from services.orchestrator.app.planning import ResearchPlan
from services.orchestrator.app.planning.planner import research_plan_from_serialized_payload
from services.orchestrator.app.query_intent_signals import (
    detect_report_archetype,
    query_has_lexical_recency_or_update_markers,
)
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
from services.orchestrator.app.reporting.structured_llm_synthesis.diagnostics import (
    collect_sections_rendered,
    pack_structured_llm_synthesis_diagnostics,
)
from services.orchestrator.app.reporting.structured_llm_synthesis.invoke import (
    invoke_structured_synthesis_bundle,
)
from services.orchestrator.app.reporting.structured_llm_synthesis.render import (
    append_to_rendered_markdown,
    render_validated_bundle_markdown,
)
from services.orchestrator.app.reporting.structured_llm_synthesis.schema import (
    StructuredSynthesisStageFlags,
)
from services.orchestrator.app.reporting.structured_llm_synthesis.validate import (
    bundle_has_renderable_content,
    validate_and_sanitize_bundle,
)
from services.orchestrator.app.research_quality import (
    build_slot_coverage_summary,
    classify_source_intent,
    contribution_level_for_counts,
    slot_ids_for_claim_category,
    source_role_for_category,
    summarize_evidence_yield,
)
from services.orchestrator.app.research_quality.readme_normalized_signals import (
    is_raw_github_readme_url,
    normalized_from_readme_from_notes,
    readme_composite_support_relation_count_from_report_claims,
)
from services.orchestrator.app.services.research_tasks import TaskNotFoundError
from services.orchestrator.app.storage import SnapshotObjectStore

REPORT_FORMAT_MARKDOWN = "markdown"
logger = get_logger(__name__)
_REPORT_REVIEW_EXCLUDE_DECISIONS = {
    "reject",
    "duplicate",
    "vague",
    "split_needed",
    "keep_context",
}
_REPORT_ACCEPT_MIN_CONFIDENCE = 0.65
_REPORT_SUPPORTING_MIN_CONFIDENCE = 0.55
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

_REPORT_COMPONENT_FOCUS_OFFICIAL_SOURCE_ROLES = frozenset(
    {
        "official_docs",
        "official_reference",
        "official_repository",
        "official_blog_or_changelog",
    }
)
_REPORT_COMPONENT_FOCUS_TECHNICAL_SLOTS = frozenset(
    {
        "architecture",
        "core_abstractions",
        "definition",
        "execution_model",
        "limitations",
        "official_sources",
        "workflow_lifecycle",
    }
)


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
    report_focus_diagnostics: dict[str, object]


@dataclass(frozen=True)
class _ReportComponentFocusEvidence:
    canonical_url: str | None
    domain: str | None
    source_role: str | None


@dataclass(frozen=True)
class _ReportComponentFocusAlignment:
    aligned: bool
    terms: tuple[str, ...] = ()
    failed_reason: str | None = None
    missing_metadata: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "aligned": self.aligned,
            "terms": list(self.terms),
        }
        if self.failed_reason:
            payload["failed_reason"] = self.failed_reason
        if self.missing_metadata:
            payload["missing_metadata"] = list(self.missing_metadata)
        return payload


class ReportSynthesisService:
    def __init__(
        self,
        session: Session,
        *,
        task_repository: ResearchTaskRepository,
        claim_repository: ClaimRepository,
        claim_evidence_repository: ClaimEvidenceRepository,
        report_artifact_repository: ReportArtifactRepository,
        task_event_repository: TaskEventRepository,
        object_store: SnapshotObjectStore,
        report_storage_bucket: str,
        llm_provider: LLMProvider | None = None,
        llm_model: str = "",
        llm_report_writer_enabled: bool = False,
        llm_report_max_output_tokens: int = 2400,
        llm_structured_synthesis_enabled: bool = False,
        llm_report_structure_enabled: bool = False,
        llm_method_card_extraction_enabled: bool = False,
        llm_comparison_table_enabled: bool = False,
        llm_synthesis_insights_enabled: bool = False,
        llm_structured_synthesis_confidence_threshold: float = 0.55,
        llm_structured_synthesis_max_input_chars: int = 12000,
        include_ledger_debug_appendix: bool = False,
    ) -> None:
        self.session = session
        self.task_repository = task_repository
        self.claim_repository = claim_repository
        self.claim_evidence_repository = claim_evidence_repository
        self.report_artifact_repository = report_artifact_repository
        self.task_event_repository = task_event_repository
        self.object_store = object_store
        self.report_storage_bucket = report_storage_bucket
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.llm_report_writer_enabled = llm_report_writer_enabled
        self.llm_report_max_output_tokens = llm_report_max_output_tokens
        self.llm_structured_synthesis_enabled = llm_structured_synthesis_enabled
        self.llm_report_structure_enabled = llm_report_structure_enabled
        self.llm_method_card_extraction_enabled = llm_method_card_extraction_enabled
        self.llm_comparison_table_enabled = llm_comparison_table_enabled
        self.llm_synthesis_insights_enabled = llm_synthesis_insights_enabled
        self.llm_structured_synthesis_confidence_threshold = (
            llm_structured_synthesis_confidence_threshold
        )
        self.llm_structured_synthesis_max_input_chars = (
            llm_structured_synthesis_max_input_chars
        )
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
            report_diagnostics=cast(
                dict[str, Any],
                {
                    "claims_by_answer_slot": report_diagnostics.get("claims_by_answer_slot", {}),
                    "claims_by_source_role": report_diagnostics.get("claims_by_source_role", {}),
                    "report_input_claims_by_slot": report_diagnostics.get(
                        "report_input_claims_by_slot", {}
                    ),
                    "report_input_claims_by_source_role": report_diagnostics.get(
                        "report_input_claims_by_source_role", {}
                    ),
                    "weak_slots_without_claims": report_diagnostics.get(
                        "weak_slots_without_claims", []
                    ),
                    "source_roles_fetched_but_not_reported": report_diagnostics.get(
                        "source_roles_fetched_but_not_reported", []
                    ),
                    "normalized_from_readme_claim_count": report_diagnostics.get(
                        "normalized_from_readme_claim_count", 0
                    ),
                    "repository_normalized_claim_count": report_diagnostics.get(
                        "repository_normalized_claim_count", 0
                    ),
                    "repository_normalized_supported_claim_count": report_diagnostics.get(
                        "repository_normalized_supported_claim_count", 0
                    ),
                    "readme_composite_support_relation_count": report_diagnostics.get(
                        "readme_composite_support_relation_count", 0
                    ),
                    "official_repository_report_input_count": report_diagnostics.get(
                        "official_repository_report_input_count", 0
                    ),
                    "raw_readme_sources_in_report_count": report_diagnostics.get(
                        "raw_readme_sources_in_report_count", 0
                    ),
                    **prepared_report.report_focus_diagnostics,
                },
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


    def _maybe_append_structured_llm_synthesis(
        self,
        *,
        rendered: RenderedMarkdownReport,
        task: ResearchTask,
        report_claims: list[ReportClaimItem],
        sources: list[ReportSourceItem],
        report_language: str,
        research_plan_payload: dict[str, object] | None,
        report_archetype: str,
        plan_intent: str | None,
    ) -> tuple[RenderedMarkdownReport, dict[str, object]]:
        master_enabled = self.llm_structured_synthesis_enabled

        def pack(
            *,
            attempted: bool,
            rendered_ok: bool,
            skipped_reason: str,
            warnings: list[str] | None = None,
            sections: list[str] | None = None,
        ) -> dict[str, object]:
            return pack_structured_llm_synthesis_diagnostics(
                enabled=master_enabled,
                attempted=attempted,
                rendered=rendered_ok,
                skipped_reason=skipped_reason,
                warnings=list(warnings or []),
                sections_rendered=list(sections or []),
            )

        if not master_enabled:
            return rendered, pack(
                attempted=False, rendered_ok=False, skipped_reason="master_disabled"
            )
        if self.llm_provider is None or isinstance(self.llm_provider, NoopLLMProvider):
            return rendered, pack(attempted=False, rendered_ok=False, skipped_reason="no_llm_provider")
        if report_archetype not in {"research_survey", "technical_comparison"}:
            return rendered, pack(attempted=False, rendered_ok=False, skipped_reason="archetype_skipped")
        flags = StructuredSynthesisStageFlags(
            structure=self.llm_report_structure_enabled,
            method_cards=self.llm_method_card_extraction_enabled,
            comparison_table=self.llm_comparison_table_enabled,
            insights=self.llm_synthesis_insights_enabled,
        )
        if not any(
            (
                flags.structure,
                flags.method_cards,
                flags.comparison_table,
                flags.insights,
            )
        ):
            return rendered, pack(attempted=False, rendered_ok=False, skipped_reason="all_subflags_off")
        if query_has_lexical_recency_or_update_markers(task.query):
            return rendered, pack(attempted=False, rendered_ok=False, skipped_reason="recency_query")
        try:
            raw = invoke_structured_synthesis_bundle(
                llm_provider=self.llm_provider,
                llm_model=self.llm_model,
                max_output_tokens=min(self.llm_report_max_output_tokens, 1600),
                task_id=task.id,
                research_question=task.query,
                report_archetype=report_archetype,
                plan_intent=plan_intent,
                research_plan=cast(dict[str, Any] | None, research_plan_payload),
                claims=report_claims,
                sources=sources,
                max_input_chars=self.llm_structured_synthesis_max_input_chars,
            )
            bundle, warnings = validate_and_sanitize_bundle(
                raw,
                claims=report_claims,
                research_question=task.query,
                deterministic_archetype=report_archetype,
                confidence_threshold=self.llm_structured_synthesis_confidence_threshold,
                flags=flags,
            )
            warn_list = list(warnings)
            if bundle is None or not bundle_has_renderable_content(bundle, flags):
                skipped = "validation_failed"
                if warn_list == ["recency_lexical_skips_structured_synthesis"]:
                    skipped = "recency_query"
                elif any(w.startswith("schema_validation_error") for w in warn_list):
                    skipped = "schema_validation_failed"
                elif "missing_archetype_judge" in warn_list:
                    skipped = "missing_archetype_judge"
                elif "archetype_confidence_below_threshold" in warn_list:
                    skipped = "archetype_confidence_below_threshold"
                elif "invalid_archetype_enum" in warn_list:
                    skipped = "invalid_archetype_enum"
                elif bundle is not None and not bundle_has_renderable_content(bundle, flags):
                    skipped = "no_renderable_content_after_validation"
                return rendered, pack(
                    attempted=True,
                    rendered_ok=False,
                    skipped_reason=skipped,
                    warnings=warn_list,
                )
            fragment = render_validated_bundle_markdown(
                bundle,
                claims=report_claims,
                base_markdown=rendered.markdown,
                report_language=report_language,
                flags=flags,
            )
            sections = collect_sections_rendered(bundle, flags)
            return append_to_rendered_markdown(rendered, fragment=fragment), pack(
                attempted=True,
                rendered_ok=True,
                skipped_reason="",
                warnings=warn_list,
                sections=sections,
            )
        except LLMError as error:
            logger.warning(
                "report.structured_llm_synthesis_failed",
                extra={"task_id": str(task.id), "error": error.to_payload()},
            )
            return rendered, pack(
                attempted=True,
                rendered_ok=False,
                skipped_reason="llm_error",
                warnings=[f"llm_error:{error.error_code}"],
            )
        except (json.JSONDecodeError, ValueError, TypeError) as error:
            logger.warning(
                "report.structured_llm_synthesis_parse_failed",
                extra={"task_id": str(task.id), "error": str(error)},
            )
            return rendered, pack(
                attempted=True,
                rendered_ok=False,
                skipped_reason="parse_error",
                warnings=["parse_error"],
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
        focus_diag = _new_report_focus_diagnostics()
        report_filter_counts: dict[str, int] = {
            "excluded_from_report_example_misaligned": 0,
            "excluded_from_report_weak_support": 0,
        }
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
                _record_report_exclusion_counts(
                    report_filter_counts,
                    reasons=["not_claimable_statement"],
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
                _record_report_exclusion_counts(
                    report_filter_counts,
                    reasons=["low_quality_or_off_query_score"],
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
                source_classification = classify_source_intent(
                    canonical_url=source_document.canonical_url,
                    domain=source_document.domain,
                    title=source_document.title,
                    query=task.query,
                )
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
                    source_role=source_classification.source_role,
                    source_intent=source_classification.source_intent,
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
                focus_diag=focus_diag,
            )
            component_focus = eligibility.get("component_focus")
            _set_report_eligibility(
                claim,
                eligible=eligibility["eligible"],
                reasons=cast(list[str], eligibility["reasons"]),
                slot_ids=slot_ids,
                review_decision=review_decision,
                component_focus=(
                    cast(dict[str, object], component_focus)
                    if isinstance(component_focus, dict)
                    else None
                ),
            )
            if not eligibility["eligible"]:
                _record_report_exclusion_counts(
                    report_filter_counts,
                    reasons=cast(list[str], eligibility["reasons"]),
                    review_decision=review_decision,
                )
                excluded_low_quality_claim_count += 1
                continue
            for evidence in support_evidence + contradict_evidence:
                source_items[evidence.source_document_id] = ReportSourceItem(
                    source_document_id=evidence.source_document_id,
                    canonical_url=evidence.canonical_url,
                    domain=evidence.domain,
                    title=None,
                    source_role=evidence.source_role,
                    source_intent=evidence.source_intent,
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
                    normalized_from_readme=normalized_from_readme_from_notes(
                        claim.notes_json if isinstance(claim.notes_json, dict) else None
                    ),
                )
            )

        sources = list(source_items.values())
        self.session.flush()
        research_plan = self._latest_existing_research_plan(task)
        research_plan_payload = research_plan.to_payload() if research_plan else None
        plan_intent: str | None = None
        if isinstance(research_plan_payload, dict):
            raw_pi = research_plan_payload.get("intent")
            if isinstance(raw_pi, str) and raw_pi.strip():
                plan_intent = raw_pi.strip()
        domain_list = [s.domain for s in sources if s.domain]
        report_archetype = detect_report_archetype(
            task.query,
            plan_intent=plan_intent,
            source_domains=domain_list,
        )
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
        report_filter_counts["excluded_from_report_weak_support"] = sum(
            1
            for claim in report_claims
            if claim.verification_status == "supported" and claim.support_level == "weak"
        )
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
                    original_user_question=task.query,
                    research_plan=research_plan_payload,
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
        rendered, structured_diag = self._maybe_append_structured_llm_synthesis(
            rendered=rendered,
            task=task,
            report_claims=report_claims,
            sources=sources,
            report_language=report_language,
            research_plan_payload=research_plan_payload,
            report_archetype=report_archetype,
            plan_intent=plan_intent,
        )
        report_writer = {
            **report_writer,
            "report_archetype": report_archetype,
            "report_filter_summary": dict(report_filter_counts),
            "critic_result": rendered.critic_result or {},
            "synthesis_plan": rendered.synthesis_plan or {},
            "redundancy_clusters": rendered.redundancy_clusters or [],
            "structured_llm_synthesis": structured_diag,
        }
        finalized_focus = _finalize_report_focus_diagnostics(focus_diag)
        return PreparedReport(
            rendered=rendered,
            claims=report_claims,
            sources=sources,
            report_language=report_language,
            report_writer=report_writer,
            report_focus_diagnostics=finalized_focus,
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

    def _latest_existing_research_plan(self, task: ResearchTask) -> ResearchPlan | None:
        for event in reversed(self.task_event_repository.list_for_task(task.id)):
            if event.event_type != "research_plan.created":
                continue
            payload = event.payload_json or {}
            if not isinstance(payload, dict):
                continue
            changes = payload.get("changes")
            if isinstance(changes, dict):
                revision_no = changes.get("revision_no")
                if isinstance(revision_no, int) and revision_no != task.revision_no:
                    continue
            result = payload.get("result")
            if not isinstance(result, dict):
                continue
            plan_payload = result.get("research_plan")
            if not isinstance(plan_payload, dict):
                continue
            return research_plan_from_serialized_payload(plan_payload)
        return None


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
    from services.orchestrator.app.settings import get_settings

    cfg = get_settings()
    return ReportSynthesisService(
        session,
        task_repository=ResearchTaskRepository(session),
        claim_repository=ClaimRepository(session),
        claim_evidence_repository=ClaimEvidenceRepository(session),
        report_artifact_repository=ReportArtifactRepository(session),
        task_event_repository=TaskEventRepository(session),
        object_store=object_store,
        report_storage_bucket=report_storage_bucket,
        llm_provider=llm_provider,
        llm_model=llm_model,
        llm_report_writer_enabled=llm_report_writer_enabled,
        llm_report_max_output_tokens=llm_report_max_output_tokens,
        llm_structured_synthesis_enabled=cfg.llm_structured_synthesis_enabled,
        llm_report_structure_enabled=cfg.llm_report_structure_enabled,
        llm_method_card_extraction_enabled=cfg.llm_method_card_extraction_enabled,
        llm_comparison_table_enabled=cfg.llm_comparison_table_enabled,
        llm_synthesis_insights_enabled=cfg.llm_synthesis_insights_enabled,
        llm_structured_synthesis_confidence_threshold=(
            cfg.llm_structured_synthesis_confidence_threshold
        ),
        llm_structured_synthesis_max_input_chars=cfg.llm_structured_synthesis_max_input_chars,
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
    focus_diag: dict[str, object] | None = None,
) -> dict[str, object]:
    reasons: list[str] = []
    if normalized_status == "draft":
        reasons.append("not_verified")
    if not support_evidence and not contradict_evidence:
        reasons.append("missing_persisted_evidence")
    decision = _string_or_none(review_decision.get("decision"))
    if _review_decision_excluded_from_report(decision=decision, slot_ids=slot_ids):
        reasons.append(f"claim_review_{decision}")
    if decision in {"keep_main", "keep_supporting"}:
        confidence = _numeric_note(review_decision.get("confidence"))
        review_reasons = review_decision.get("reasons")
        related_answer_slot = _string_or_none(review_decision.get("related_answer_slot"))
        covered_slot_ids = review_decision.get("covered_slot_ids")
        quality_flags = review_decision.get("quality_flags")
        minimum_confidence = (
            _REPORT_ACCEPT_MIN_CONFIDENCE
            if decision == "keep_main"
            else _REPORT_SUPPORTING_MIN_CONFIDENCE
        )
        if confidence is None or confidence < minimum_confidence:
            reasons.append(f"claim_review_low_confidence_{decision}")
        if not isinstance(review_reasons, list) or not any(
            isinstance(item, str) and item.strip() for item in review_reasons
        ):
            reasons.append("claim_review_missing_reasons")
        valid_review_slot = bool(related_answer_slot and related_answer_slot in set(slot_ids))
        if not valid_review_slot and isinstance(covered_slot_ids, list):
            valid_review_slot = any(
                isinstance(item, str) and item.strip() and item in set(slot_ids)
                for item in covered_slot_ids
            )
        if slot_ids and not valid_review_slot:
            reasons.append("claim_review_missing_slot_coverage")
        if isinstance(quality_flags, list) and quality_flags:
            reasons.append("claim_review_quality_flags")
        if decision == "keep_main" and review_decision.get("relevance") not in {
            "direct",
            "partial",
        }:
            reasons.append("claim_review_main_not_direct")
        if review_decision.get("source_role") == "unsuitable":
            reasons.append("claim_review_unsuitable_source")
    elif decision in {"accept", "downrank"}:
        confidence = _numeric_note(review_decision.get("confidence"))
        if decision == "accept" and (
            confidence is None or confidence < _REPORT_ACCEPT_MIN_CONFIDENCE
        ):
            reasons.append("claim_review_low_confidence_accept")
    if _event_or_announcement_noise_claim(claim.statement, query=query):
        reasons.append("event_or_announcement_noise")
    if claim_score.rejected_reason:
        reasons.append(f"claim_score_rejected:{claim_score.rejected_reason}")
    focus_required = _claim_focus_required_for_report(query=query, slot_ids=slot_ids)
    direct_focus = (
        _claim_focus_matches_query(claim.statement, query=query) if focus_required else True
    )
    component_ok = False
    component_terms: list[str] = []
    component_alignment: _ReportComponentFocusAlignment | None = None
    if focus_required and not direct_focus:
        if focus_diag is not None:
            focus_diag["report_component_focus_evaluated_count"] = (
                int(focus_diag.get("report_component_focus_evaluated_count") or 0) + 1
            )
        component_alignment = _technical_component_focus_alignment(
            statement=claim.statement,
            query=query,
            slot_ids=slot_ids,
            support_evidence=support_evidence,
            claim_notes=claim.notes_json if isinstance(claim.notes_json, dict) else {},
        )
        component_ok = component_alignment.aligned
        component_terms = list(component_alignment.terms)
        if component_ok and focus_diag is not None:
            focus_diag["report_focus_component_match_count"] = (
                int(focus_diag.get("report_focus_component_match_count") or 0) + 1
            )
            focus_diag["report_component_focus_rescued_count"] = (
                int(focus_diag.get("report_component_focus_rescued_count") or 0) + 1
            )
            focus_diag["report_query_focus_rescued_by_component_count"] = (
                int(focus_diag.get("report_query_focus_rescued_by_component_count") or 0) + 1
            )
            raw_hits = focus_diag.get("_component_term_hits")
            if not isinstance(raw_hits, set):
                raw_hits = set()
                focus_diag["_component_term_hits"] = raw_hits
            for term in component_terms:
                if isinstance(term, str) and term.strip():
                    raw_hits.add(term.strip())
        elif focus_diag is not None:
            _record_component_focus_failure(focus_diag, component_alignment)
    if focus_required and not (direct_focus or component_ok):
        reasons.append("query_focus_mismatch")
        if focus_diag is not None:
            focus_diag["report_query_focus_mismatch_count"] = (
                int(focus_diag.get("report_query_focus_mismatch_count") or 0) + 1
            )
            by_slot = focus_diag.get("report_query_focus_mismatch_by_slot")
            if not isinstance(by_slot, dict):
                by_slot = {}
                focus_diag["report_query_focus_mismatch_by_slot"] = by_slot
            slot_labels = slot_ids or ["unknown"]
            for slot in slot_labels:
                if not isinstance(slot, str) or not slot.strip():
                    continue
                sid = slot.strip()
                by_slot[sid] = int(by_slot.get(sid) or 0) + 1
    result: dict[str, object] = {"eligible": not reasons, "reasons": list(dict.fromkeys(reasons))}
    if component_alignment is not None:
        result["component_focus"] = component_alignment.to_payload()
    return result


def _review_decision_excluded_from_report(*, decision: str | None, slot_ids: list[str]) -> bool:
    if decision in _REPORT_REVIEW_EXCLUDE_DECISIONS:
        return True
    if decision == "keep_example":
        return "examples_use_cases" not in set(slot_ids)
    return False


def _set_report_eligibility(
    claim: Claim,
    *,
    eligible: bool,
    reasons: list[str],
    slot_ids: list[str],
    review_decision: dict[str, object],
    component_focus: dict[str, object] | None = None,
) -> None:
    notes = claim.notes_json or {}
    eligibility: dict[str, object] = {
        "eligible": eligible,
        "reasons": reasons,
        "slot_ids": slot_ids,
        "review_decision": review_decision,
    }
    if component_focus is not None:
        eligibility["component_focus"] = component_focus
    claim.notes_json = {
        **notes,
        "report_eligible": eligible,
        "report_eligibility": eligibility,
    }


def _record_report_exclusion_counts(
    counts: dict[str, int],
    *,
    reasons: list[str],
    review_decision: dict[str, object],
) -> None:
    decision = _string_or_none(review_decision.get("decision"))
    claim_role = _string_or_none(review_decision.get("claim_role"))
    centrality = _string_or_none(review_decision.get("centrality"))
    if (
        decision == "keep_example"
        or claim_role == "example"
        or centrality == "example"
        or "claim_review_keep_example" in reasons
    ):
        counts["excluded_from_report_example_misaligned"] = (
            counts.get("excluded_from_report_example_misaligned", 0) + 1
        )


def _report_claim_slot_ids(
    claim: Claim,
    *,
    claim_score: ClaimCandidateScore,
    query: str,
) -> list[str]:
    notes = claim.notes_json or {}
    noted_slots = _string_list_note(notes.get("slot_ids"))
    evidence_candidate = notes.get("evidence_candidate")
    if isinstance(evidence_candidate, dict):
        noted_slots.extend(_string_list_note(evidence_candidate.get("slot_ids")))
        metadata = evidence_candidate.get("metadata")
        if isinstance(metadata, dict):
            noted_slots.extend(_string_list_note(metadata.get("slot_ids")))
    if noted_slots:
        return list(dict.fromkeys(noted_slots))
    return _slot_ids_from_claim_category(claim_score.claim_category, query=query)


def _claim_review_decision(notes: dict[str, object]) -> dict[str, object]:
    review = notes.get("llm_claim_review")
    if not isinstance(review, dict):
        return {}
    return _normalize_report_review_decision(review)


def _normalize_report_review_decision(review: dict[str, object]) -> dict[str, object]:
    raw_decision = _string_or_none(review.get("decision")) or ""
    normalized_decision = raw_decision.strip().lower().replace(" ", "_")
    decision_aliases = {
        "accept": "keep_main",
        "accepted": "keep_main",
        "approve": "keep_main",
        "approved": "keep_main",
        "downrank": "keep_supporting",
        "lower_priority": "keep_supporting",
        "duplicate": "reject",
        "split_needed": "reject",
        "too_vague": "reject",
        "vague": "reject",
    }
    decision = decision_aliases.get(normalized_decision, normalized_decision)
    if decision not in {"keep_main", "keep_supporting", "keep_example", "keep_context", "reject"}:
        decision = (
            normalized_decision if normalized_decision in _REPORT_REVIEW_EXCLUDE_DECISIONS else ""
        )
    reasons = _string_list_note(review.get("reasons"))
    if not reasons:
        reason = _string_or_none(review.get("reason")) or _string_or_none(review.get("rationale"))
        reasons = [reason] if reason else []
    covered_slot_ids = _string_list_note(review.get("covered_slot_ids"))
    if not covered_slot_ids:
        covered_slot_ids = _string_list_note(review.get("slots"))
    related_answer_slot = _string_or_none(review.get("related_answer_slot"))
    if related_answer_slot is None and covered_slot_ids:
        related_answer_slot = covered_slot_ids[0]
    normalized = {
        **review,
        "decision": decision,
        "reasons": reasons,
        "covered_slot_ids": covered_slot_ids,
        "related_answer_slot": related_answer_slot,
        "relevance": _normalized_review_enum(
            review.get("relevance"),
            allowed={"direct", "partial", "background", "off_topic"},
            default=("direct" if decision == "keep_main" else "partial"),
        ),
        "source_role": _normalized_review_enum(
            review.get("source_role"),
            allowed={"primary_reference", "supporting_reference", "example_only", "unsuitable"},
            default=("primary_reference" if decision == "keep_main" else "supporting_reference"),
        ),
        "claim_role": _normalized_review_enum(
            review.get("claim_role"),
            allowed={
                "definition",
                "component",
                "mechanism",
                "application",
                "comparison",
                "limitation",
                "example",
            },
            default="mechanism",
        ),
        "centrality": _normalized_review_enum(
            review.get("centrality"),
            allowed={"core", "supporting", "example", "peripheral"},
            default=("core" if decision == "keep_main" else "supporting"),
        ),
    }
    if normalized["relevance"] == "off_topic" or normalized["source_role"] == "unsuitable":
        normalized["decision"] = "reject"
    return normalized


def _normalized_review_enum(
    value: object,
    *,
    allowed: set[str],
    default: str,
) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower().replace(" ", "_")
        if normalized in allowed:
            return normalized
    return default


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


def _new_report_focus_diagnostics() -> dict[str, object]:
    return {
        "report_focus_component_match_count": 0,
        "report_focus_component_match_terms": [],
        "report_component_focus_evaluated_count": 0,
        "report_component_focus_rescued_count": 0,
        "report_component_focus_failed_reason_distribution": {},
        "report_component_focus_missing_metadata_distribution": {},
        "report_query_focus_mismatch_count": 0,
        "report_query_focus_mismatch_by_slot": {},
        "report_query_focus_rescued_by_component_count": 0,
        "_component_term_hits": set(),
    }


def _finalize_report_focus_diagnostics(diag: dict[str, object]) -> dict[str, object]:
    raw_hits = diag.get("_component_term_hits")
    if isinstance(raw_hits, set):
        term_hits = {str(item) for item in raw_hits if isinstance(item, str) and item.strip()}
        diag["report_focus_component_match_terms"] = sorted(term_hits)
    diag.pop("_component_term_hits", None)
    by_slot = diag.get("report_query_focus_mismatch_by_slot")
    if isinstance(by_slot, dict):
        diag["report_query_focus_mismatch_by_slot"] = dict(
            sorted(
                ((str(k), int(v)) for k, v in by_slot.items() if isinstance(k, str)),
                key=lambda item: item[0],
            )
        )
    for key in (
        "report_component_focus_failed_reason_distribution",
        "report_component_focus_missing_metadata_distribution",
    ):
        value = diag.get(key)
        if isinstance(value, dict):
            diag[key] = dict(
                sorted(
                    ((str(k), int(v)) for k, v in value.items() if isinstance(k, str)),
                    key=lambda item: item[0],
                )
            )
    return diag


def _record_component_focus_failure(
    focus_diag: dict[str, object],
    alignment: _ReportComponentFocusAlignment,
) -> None:
    reason = alignment.failed_reason or "unknown"
    reasons = focus_diag.get("report_component_focus_failed_reason_distribution")
    if not isinstance(reasons, dict):
        reasons = {}
        focus_diag["report_component_focus_failed_reason_distribution"] = reasons
    reasons[reason] = int(reasons.get(reason) or 0) + 1
    missing = focus_diag.get("report_component_focus_missing_metadata_distribution")
    if not isinstance(missing, dict):
        missing = {}
        focus_diag["report_component_focus_missing_metadata_distribution"] = missing
    for item in alignment.missing_metadata:
        missing[item] = int(missing.get(item) or 0) + 1


def _langgraph_official_report_evidence(ev: object) -> bool:
    url = (ev.canonical_url or "").lower()
    domain = (ev.domain or "").strip().lower().removeprefix("www.")
    if not url or not domain:
        return False
    if domain == "github.com" and "langchain-ai/langgraph" in url:
        return True
    if "langgraph" in url and domain.endswith("langchain.com"):
        return True
    return False


@dataclass(frozen=True)
class _ReportComponentFocusEntity:
    query_focus_terms: frozenset[str]
    subject_terms: frozenset[str]
    component_tokens: frozenset[str]
    anchor_tokens: frozenset[str]
    component_phrases: tuple[str, ...]
    official_evidence: Callable[[object], bool]


_LANGGRAPH_COMPONENT_TOKENS = frozenset(
    {
        "stategraph",
        "messagegraph",
        "graph",
        "node",
        "edge",
        "state",
        "checkpoint",
        "checkpointer",
        "reducer",
        "command",
        "interrupt",
        "persistence",
    }
)
_LANGGRAPH_COMPONENT_ANCHOR_TOKENS = frozenset(
    {
        "stategraph",
        "messagegraph",
        "checkpointer",
        "checkpoint",
        "reducer",
        "command",
        "interrupt",
        "persistence",
    }
)

_REPORT_COMPONENT_FOCUS_ENTITIES: dict[str, _ReportComponentFocusEntity] = {
    "langgraph": _ReportComponentFocusEntity(
        query_focus_terms=frozenset({"langgraph"}),
        subject_terms=frozenset({"langgraph"}),
        component_tokens=_LANGGRAPH_COMPONENT_TOKENS,
        anchor_tokens=_LANGGRAPH_COMPONENT_ANCHOR_TOKENS,
        component_phrases=("durable execution",),
        official_evidence=_langgraph_official_report_evidence,
    ),
}


def _resolve_report_component_focus_entity(
    *, query: str, intent: object
) -> _ReportComponentFocusEntity | None:
    subject_terms = getattr(intent, "subject_terms", ()) or ()
    lowered_subjects = {str(item).lower() for item in subject_terms if isinstance(item, str)}
    for entity in _REPORT_COMPONENT_FOCUS_ENTITIES.values():
        if lowered_subjects & entity.subject_terms:
            return entity
    required = _query_required_focus_terms(query)
    for entity in _REPORT_COMPONENT_FOCUS_ENTITIES.values():
        if required & entity.query_focus_terms:
            return entity
    return None


def _technical_component_focus_alignment(
    *,
    statement: str,
    query: str,
    slot_ids: list[str],
    support_evidence: list[ReportEvidenceItem],
    claim_notes: dict[str, object] | None = None,
) -> _ReportComponentFocusAlignment:
    intent = classify_query_intent(query)
    if intent.intent_name != "definition_mechanism":
        return _ReportComponentFocusAlignment(False, failed_reason="intent_mismatch")
    if not support_evidence:
        return _ReportComponentFocusAlignment(
            False,
            failed_reason="missing_support_evidence",
            missing_metadata=("support_evidence",),
        )
    slot_set = {item for item in slot_ids if isinstance(item, str) and item.strip()}
    if not slot_set & _REPORT_COMPONENT_FOCUS_TECHNICAL_SLOTS:
        return _ReportComponentFocusAlignment(
            False,
            failed_reason="slot_mismatch",
            missing_metadata=("technical_slot",) if not slot_set else (),
        )
    entity = _resolve_report_component_focus_entity(query=query, intent=intent)
    if entity is None:
        return _ReportComponentFocusAlignment(False, failed_reason="entity_missing")
    focus_evidence = _component_focus_evidence_sources(
        support_evidence=support_evidence,
        claim_notes=claim_notes or {},
    )
    source_roles = {
        (item.source_role or "").strip()
        for item in focus_evidence
        if (item.source_role or "").strip()
    }
    if not source_roles:
        return _ReportComponentFocusAlignment(
            False,
            failed_reason="source_role_missing",
            missing_metadata=("source_role",),
        )
    if not source_roles & _REPORT_COMPONENT_FOCUS_OFFICIAL_SOURCE_ROLES:
        return _ReportComponentFocusAlignment(False, failed_reason="official_source_role_missing")
    if not any(
        (item.canonical_url or "").strip() and (item.domain or "").strip()
        for item in focus_evidence
    ):
        return _ReportComponentFocusAlignment(
            False,
            failed_reason="source_url_missing",
            missing_metadata=("source_url", "source_domain"),
        )
    if not any(entity.official_evidence(item) for item in focus_evidence):
        return _ReportComponentFocusAlignment(False, failed_reason="official_url_check_failed")
    lowered = statement.lower()
    tokens = set(_tokenize_focus(statement))
    matched: list[str] = []
    phrase_hit = False
    for phrase in entity.component_phrases:
        if phrase in lowered:
            phrase_hit = True
            matched.append(phrase)
    weak_tokens = entity.component_tokens - entity.anchor_tokens
    anchor_hits = sorted(tokens & entity.anchor_tokens)
    matched.extend(anchor_hits)
    weak_hits = tokens & weak_tokens
    if not phrase_hit and not anchor_hits and len(weak_hits) < 2:
        return _ReportComponentFocusAlignment(False, failed_reason="component_term_mismatch")
    for tok in sorted(weak_hits):
        if tok not in matched:
            matched.append(tok)
    matched = list(dict.fromkeys(matched))
    if not matched:
        return _ReportComponentFocusAlignment(False, failed_reason="component_term_mismatch")
    return _ReportComponentFocusAlignment(True, terms=tuple(matched))


def _component_focus_evidence_sources(
    *,
    support_evidence: list[ReportEvidenceItem],
    claim_notes: dict[str, object],
) -> list[object]:
    sources: list[object] = list(support_evidence)
    for source in _component_focus_evidence_from_claim_notes(claim_notes):
        sources.append(source)
    return sources


def _component_focus_evidence_from_claim_notes(
    notes: dict[str, object],
) -> list[_ReportComponentFocusEvidence]:
    rows: list[_ReportComponentFocusEvidence] = []

    def add_from_values(
        *,
        canonical_url: object,
        domain: object,
        source_role: object,
        source_intent: object = None,
    ) -> None:
        url_value = canonical_url if isinstance(canonical_url, str) else None
        domain_value = domain if isinstance(domain, str) else None
        role_value = source_role if isinstance(source_role, str) and source_role.strip() else None
        if role_value is None and isinstance(source_intent, str) and source_intent.strip():
            role_value = source_role_for_category(
                source_intent,
                canonical_url=url_value or "",
                domain=domain_value,
            )
        if role_value is None and url_value and domain_value:
            role_value = classify_source_intent(
                canonical_url=url_value,
                domain=domain_value,
                title=None,
                query=None,
            ).source_role
        if url_value or domain_value or role_value:
            rows.append(
                _ReportComponentFocusEvidence(
                    canonical_url=url_value,
                    domain=domain_value,
                    source_role=role_value,
                )
            )

    add_from_values(
        canonical_url=notes.get("source_url"),
        domain=notes.get("source_domain"),
        source_role=notes.get("source_role"),
        source_intent=notes.get("source_intent"),
    )
    evidence_candidate = notes.get("evidence_candidate")
    if isinstance(evidence_candidate, dict):
        add_from_values(
            canonical_url=evidence_candidate.get("source_url"),
            domain=evidence_candidate.get("source_domain"),
            source_role=evidence_candidate.get("source_role"),
            source_intent=evidence_candidate.get("source_intent"),
        )
        metadata = evidence_candidate.get("metadata")
        if isinstance(metadata, dict):
            add_from_values(
                canonical_url=metadata.get("source_url"),
                domain=metadata.get("source_domain"),
                source_role=metadata.get("source_role"),
                source_intent=evidence_candidate.get("source_intent")
                or metadata.get("source_intent"),
            )
    verification = notes.get("verification")
    if isinstance(verification, dict):
        relations = verification.get("evidence_relations")
        if isinstance(relations, list):
            for row in relations:
                if not isinstance(row, dict):
                    continue
                add_from_values(
                    canonical_url=row.get("source_url"),
                    domain=row.get("source_domain"),
                    source_role=row.get("source_role"),
                    source_intent=row.get("source_intent"),
                )
    return rows


def _claim_score_from_notes(notes: dict[str, object]) -> ClaimCandidateScore | None:
    claim_quality_score = _numeric_note(notes.get("claim_quality_score"))
    query_answer_score = _numeric_note(notes.get("query_answer_score"))
    if claim_quality_score is None or query_answer_score is None:
        return None

    claim_category = notes.get("claim_category")
    rejected_reason = notes.get("rejected_reason")
    answer_role = notes.get("answer_role")
    answer_relevant = notes.get("answer_relevant")

    from services.orchestrator.app.claims.drafting import CandidateTriageStatus

    triage_status_val = notes.get("triage_status")
    if isinstance(triage_status_val, str):
        try:
            triage_status = CandidateTriageStatus(triage_status_val)
        except ValueError:
            triage_status = (
                CandidateTriageStatus.REJECT_FATAL
                if rejected_reason
                else CandidateTriageStatus.ACCEPT_CANDIDATE
            )
    else:
        triage_status = (
            CandidateTriageStatus.REJECT_FATAL
            if rejected_reason
            else CandidateTriageStatus.ACCEPT_CANDIDATE
        )

    return ClaimCandidateScore(
        claim_category=claim_category if isinstance(claim_category, str) else "other",
        answer_role=answer_role if isinstance(answer_role, str) else "non_answer",
        answer_relevant=answer_relevant if isinstance(answer_relevant, bool) else False,
        content_quality_score=_numeric_note(notes.get("content_quality_score")) or 0.6,
        query_relevance_score=_numeric_note(notes.get("query_relevance_score")) or 0.0,
        claim_quality_score=claim_quality_score,
        query_answer_score=query_answer_score,
        source_quality_score=_numeric_note(notes.get("source_quality_score")) or 0.5,
        source_suitability_score=_numeric_note(notes.get("source_suitability_score")) or 0.5,
        final_score=_numeric_note(notes.get("claim_selection_score")) or 0.0,
        candidate_tier=(
            notes.get("candidate_tier")
            if isinstance(notes.get("candidate_tier"), str)
            else "main_candidate"
        ),
        rejected_reason=rejected_reason if isinstance(rejected_reason, str) else None,
        triage_status=triage_status,
        analysis_flags=tuple(
            item
            for item in notes.get("analysis_flags", [])
            if isinstance(item, str) and item.strip()
        ),
    )


def _numeric_note(value: object) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    return None


def _string_list_note(value: object) -> list[str]:
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]


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
            "source_role": (
                claim.support_evidence[0].source_role
                if claim.support_evidence and claim.support_evidence[0].source_role
                else "report_evidence_source"
            ),
            "normalized_from_readme": claim.normalized_from_readme,
            "support_level": claim.support_level or "strong",
        }
        for claim in claims
    ]
    normalized_readme_claim_total = sum(
        1 for claim in claims if claim.normalized_from_readme
    )
    normalized_readme_supported_total = sum(
        1
        for claim in claims
        if claim.normalized_from_readme and claim.verification_status == "supported"
    )
    official_repository_report_input_total = sum(
        1
        for claim in claims
        if claim.support_evidence
        and str(claim.support_evidence[0].source_role or "").strip() == "official_repository"
    )
    raw_readme_sources_total = len(
        {
            s.source_document_id
            for s in sources
            if is_raw_github_readme_url(s.canonical_url)
        }
    )
    readme_composite_relations = readme_composite_support_relation_count_from_report_claims(
        claims
    )
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
    slot_coverage_summary = build_slot_coverage_summary(
        query,
        evidence_candidates=evidence_candidate_rows,
        claim_rows=claim_rows,
    )
    claims_by_answer_slot = _count_claim_rows_by_slot(claim_rows)
    claims_by_source_role = _count_claim_rows_by_source_role(claim_rows)
    weak_slots_without_claims = sorted(
        {
            str(row.get("slot_id"))
            for row in slot_coverage_summary
            if row.get("status") == "weak"
            and int(row.get("supported_claim_count") or 0)
            + int(row.get("weak_supported_claim_count") or 0)
            <= 0
        }
    )
    return {
        "slot_coverage_summary": slot_coverage_summary,
        "evidence_yield_summary": summarize_evidence_yield(
            evidence_candidate_rows,
            accepted_candidate_ids=accepted_candidate_ids,
            query=query,
        ),
        "source_yield_summary": source_yield_summary,
        "claims_by_answer_slot": claims_by_answer_slot,
        "claims_by_source_role": claims_by_source_role,
        "report_input_claims_by_slot": claims_by_answer_slot,
        "report_input_claims_by_source_role": claims_by_source_role,
        "weak_slots_without_claims": weak_slots_without_claims,
        "source_roles_fetched_but_not_reported": sorted(
            {
                str(row.get("source_role"))
                for row in source_yield_summary
                if row.get("contribution_level") == "none"
                and row.get("dropped_reasons")
                and isinstance(row.get("source_role"), str)
                and str(row.get("source_role")).strip()
            }
        ),
        "normalized_from_readme_claim_count": normalized_readme_claim_total,
        "repository_normalized_claim_count": normalized_readme_claim_total,
        "repository_normalized_supported_claim_count": normalized_readme_supported_total,
        "readme_composite_support_relation_count": readme_composite_relations,
        "official_repository_report_input_count": official_repository_report_input_total,
        "raw_readme_sources_in_report_count": raw_readme_sources_total,
        "verification_summary": _report_verification_summary(
            claims,
            readme_composite_support_relation_count=readme_composite_relations,
        ),
        "dropped_sources": [
            row
            for row in source_yield_summary
            if row.get("contribution_level") == "none" and row.get("dropped_reasons")
        ],
    }


def _count_claim_rows_by_slot(claim_rows: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in claim_rows:
        for slot_id in row.get("slot_ids") or []:
            if not isinstance(slot_id, str) or not slot_id.strip():
                continue
            counts[slot_id] = counts.get(slot_id, 0) + 1
    return dict(sorted(counts.items()))


def _count_claim_rows_by_source_role(claim_rows: list[dict[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in claim_rows:
        source_role = row.get("source_role")
        if not isinstance(source_role, str) or not source_role.strip():
            continue
        counts[source_role] = counts.get(source_role, 0) + 1
    return dict(sorted(counts.items()))


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
        "source_role": evidence.source_role or "report_evidence_source",
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
            "source_role": evidence.source_role,
            "source_intent": evidence.source_intent,
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
                "source_intent": source.source_intent or "report_evidence_source",
                "source_role": source.source_role
                or source.source_intent
                or "report_evidence_source",
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


def _report_verification_summary(
    claims: list[ReportClaimItem],
    *,
    readme_composite_support_relation_count: int = 0,
) -> dict[str, object]:
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
        "readme_composite_support_relation_count": int(readme_composite_support_relation_count),
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
