from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.orm import Session

from packages.db.models import ContentSnapshot, ResearchTask, SourceChunk, SourceDocument
from packages.db.repositories import (
    ContentSnapshotRepository,
    ResearchTaskRepository,
    SourceChunkRepository,
    SourceDocumentRepository,
)
from packages.observability import get_logger, record_parse_results
from services.orchestrator.app.parsing import (
    ParseResultReason,
    UnsupportedMimeTypeError,
    assess_chunk_quality,
    assess_source_quality,
    chunk_text,
    extract_parsed_content,
)
from services.orchestrator.app.services.acquisition import FETCH_STATUS_SUCCEEDED
from services.orchestrator.app.services.research_tasks import (
    PHASE2_ACTIVE_STATUS,
    TaskNotFoundError,
)
from services.orchestrator.app.storage import SnapshotObjectStore

MAX_SNAPSHOTS_PER_REQUEST = 10

logger = get_logger(__name__)

PARSE_DECISION_PARSED = "parsed"
PARSE_DECISION_SKIPPED_EMPTY = "skipped_empty"
PARSE_DECISION_SKIPPED_UNSUPPORTED_MIME = "skipped_unsupported_mime"
PARSE_DECISION_MISSING_BLOB = "missing_blob"
PARSE_DECISION_PARSE_ERROR = "parse_error"
PARSE_DECISION_ALREADY_PARSED = "already_parsed"
PARSE_DECISION_FETCH_NOT_SUCCEEDED = "fetch_not_succeeded"


class ParsingConflictError(Exception):
    def __init__(
        self,
        task_id: UUID,
        current_status: str,
        allowed_statuses: tuple[str, ...] | None = None,
    ) -> None:
        allowed_text = ""
        if allowed_statuses:
            allowed_text = f"; allowed statuses: {', '.join(allowed_statuses)}"
        super().__init__(
            f"cannot parse snapshots for task {task_id} from status {current_status}"
            f"{allowed_text}"
        )
        self.task_id = task_id
        self.current_status = current_status
        self.allowed_statuses = allowed_statuses or ()


class ContentSnapshotNotFoundError(Exception):
    def __init__(self, task_id: UUID, content_snapshot_id: UUID) -> None:
        super().__init__(f"content_snapshot {content_snapshot_id} was not found for task {task_id}")
        self.task_id = task_id
        self.content_snapshot_id = content_snapshot_id


@dataclass(frozen=True)
class ParseLedgerEntry:
    content_snapshot: ContentSnapshot
    source_document: SourceDocument | None
    chunks_created: int
    status: str
    reason: ParseResultReason | None
    updated_existing: bool
    decision: str
    body_length: int | None = None
    parser_error: str | None = None


@dataclass(frozen=True)
class ParseBatchResult:
    task_id: UUID
    created: int
    updated: int
    skipped_existing: int
    skipped_unsupported: int
    failed: int
    entries: list[ParseLedgerEntry]


class ParsingService:
    def __init__(
        self,
        session: Session,
        *,
        task_repository: ResearchTaskRepository,
        content_snapshot_repository: ContentSnapshotRepository,
        source_document_repository: SourceDocumentRepository,
        source_chunk_repository: SourceChunkRepository,
        snapshot_object_store: SnapshotObjectStore,
        allowed_statuses: tuple[str, ...] = (PHASE2_ACTIVE_STATUS,),
    ) -> None:
        self.session = session
        self.task_repository = task_repository
        self.content_snapshot_repository = content_snapshot_repository
        self.source_document_repository = source_document_repository
        self.source_chunk_repository = source_chunk_repository
        self.snapshot_object_store = snapshot_object_store
        self.allowed_statuses = allowed_statuses

    def parse_snapshots(
        self,
        task_id: UUID,
        *,
        content_snapshot_ids: list[UUID] | None,
        limit: int | None,
    ) -> ParseBatchResult:
        task = self.task_repository.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        if task.status not in self.allowed_statuses:
            raise ParsingConflictError(task.id, task.status, self.allowed_statuses)

        effective_limit = min(limit or MAX_SNAPSHOTS_PER_REQUEST, MAX_SNAPSHOTS_PER_REQUEST)
        selected_snapshots = self._select_snapshots(
            task_id,
            content_snapshot_ids=content_snapshot_ids,
            limit=effective_limit,
        )

        entries: list[ParseLedgerEntry] = []
        created = 0
        updated = 0
        skipped_existing = 0
        skipped_unsupported = 0
        failed = 0

        for content_snapshot in selected_snapshots:
            entry = self._parse_one_snapshot(task, content_snapshot)
            entries.append(entry)
            if entry.status == "CREATED":
                created += 1
            elif entry.status == "UPDATED":
                updated += 1
            elif entry.status == "SKIPPED" and entry.reason == ParseResultReason.ALREADY_PARSED:
                skipped_existing += 1
            elif (
                entry.status == "SKIPPED"
                and entry.reason == ParseResultReason.UNSUPPORTED_MIME_TYPE
            ):
                skipped_unsupported += 1
            elif entry.status == "FAILED":
                failed += 1

        record_parse_results(
            created=created,
            updated=updated,
            skipped_existing=skipped_existing,
            skipped_unsupported=skipped_unsupported,
            failed=failed,
        )
        logger.info(
            "parse.batch.completed",
            extra={
                "task_id": str(task_id),
                "created_count": created,
                "updated_count": updated,
                "skipped_existing": skipped_existing,
                "skipped_unsupported": skipped_unsupported,
                "failed": failed,
                "parse_decisions": [parse_entry_diagnostic(entry) for entry in entries],
            },
        )
        return ParseBatchResult(
            task_id=task_id,
            created=created,
            updated=updated,
            skipped_existing=skipped_existing,
            skipped_unsupported=skipped_unsupported,
            failed=failed,
            entries=entries,
        )

    def list_source_documents(
        self,
        task_id: UUID,
        *,
        limit: int | None = None,
    ) -> list[SourceDocument]:
        self._get_task_or_raise(task_id)
        return self.source_document_repository.list_for_task(task_id, limit=limit)

    def list_source_chunks(
        self,
        task_id: UUID,
        *,
        source_document_id: UUID | None = None,
        limit: int | None = None,
    ) -> list[SourceChunk]:
        self._get_task_or_raise(task_id)
        return self.source_chunk_repository.list_for_task(
            task_id,
            source_document_id=source_document_id,
            limit=limit,
        )

    def _select_snapshots(
        self,
        task_id: UUID,
        *,
        content_snapshot_ids: list[UUID] | None,
        limit: int,
    ) -> list[ContentSnapshot]:
        if content_snapshot_ids is None:
            return self.content_snapshot_repository.list_for_task(task_id, limit=limit)

        selected = self.content_snapshot_repository.list_by_ids_for_task(
            task_id,
            content_snapshot_ids,
        )
        selected_ids = {item.id for item in selected}
        ordered_snapshots: list[ContentSnapshot] = []
        seen_ids: set[UUID] = set()
        for content_snapshot_id in content_snapshot_ids:
            if content_snapshot_id in seen_ids:
                continue
            if content_snapshot_id not in selected_ids:
                raise ContentSnapshotNotFoundError(task_id, content_snapshot_id)
            ordered_snapshots.append(
                next(item for item in selected if item.id == content_snapshot_id)
            )
            seen_ids.add(content_snapshot_id)
            if len(ordered_snapshots) >= limit:
                break
        return ordered_snapshots

    def _parse_one_snapshot(
        self,
        task: ResearchTask,
        content_snapshot: ContentSnapshot,
    ) -> ParseLedgerEntry:
        task_id = task.id
        fetch_attempt = content_snapshot.fetch_attempt
        fetch_job = fetch_attempt.fetch_job
        if fetch_job.status != FETCH_STATUS_SUCCEEDED or fetch_attempt.error_code is not None:
            return ParseLedgerEntry(
                content_snapshot=content_snapshot,
                source_document=None,
                chunks_created=0,
                status="SKIPPED",
                reason=ParseResultReason.FETCH_NOT_SUCCEEDED,
                updated_existing=False,
                decision=PARSE_DECISION_FETCH_NOT_SUCCEEDED,
            )

        existing_for_snapshot = self.source_document_repository.get_for_content_snapshot(
            content_snapshot.id
        )
        if existing_for_snapshot is not None:
            return ParseLedgerEntry(
                content_snapshot=content_snapshot,
                source_document=existing_for_snapshot,
                chunks_created=len(existing_for_snapshot.chunks),
                status="SKIPPED",
                reason=ParseResultReason.ALREADY_PARSED,
                updated_existing=False,
                decision=PARSE_DECISION_ALREADY_PARSED,
            )

        try:
            raw_content = self.snapshot_object_store.get_bytes(
                bucket=content_snapshot.storage_bucket,
                key=content_snapshot.storage_key,
            )
        except FileNotFoundError:
            return ParseLedgerEntry(
                content_snapshot=content_snapshot,
                source_document=None,
                chunks_created=0,
                status="FAILED",
                reason=ParseResultReason.SNAPSHOT_OBJECT_MISSING,
                updated_existing=False,
                decision=PARSE_DECISION_MISSING_BLOB,
                body_length=None,
                parser_error="snapshot object was not found in object store",
            )

        try:
            parsed_content = extract_parsed_content(
                mime_type=content_snapshot.mime_type,
                content=raw_content,
            )
        except UnsupportedMimeTypeError:
            return ParseLedgerEntry(
                content_snapshot=content_snapshot,
                source_document=None,
                chunks_created=0,
                status="SKIPPED",
                reason=ParseResultReason.UNSUPPORTED_MIME_TYPE,
                updated_existing=False,
                decision=PARSE_DECISION_SKIPPED_UNSUPPORTED_MIME,
                body_length=len(raw_content),
            )
        except Exception as error:  # noqa: BLE001 - parser diagnostics must preserve the failure.
            return ParseLedgerEntry(
                content_snapshot=content_snapshot,
                source_document=None,
                chunks_created=0,
                status="FAILED",
                reason=ParseResultReason.PARSE_ERROR,
                updated_existing=False,
                decision=PARSE_DECISION_PARSE_ERROR,
                body_length=len(raw_content),
                parser_error=str(error),
            )

        if not parsed_content.text.strip():
            return ParseLedgerEntry(
                content_snapshot=content_snapshot,
                source_document=None,
                chunks_created=0,
                status="SKIPPED",
                reason=ParseResultReason.EMPTY_EXTRACTED_TEXT,
                updated_existing=False,
                decision=PARSE_DECISION_SKIPPED_EMPTY,
                body_length=len(raw_content),
            )

        candidate_url = fetch_job.candidate_url
        document_url = _document_url_for_fetch(
            candidate_url.canonical_url, fetch_attempt.trace_json
        )
        document_domain = _domain_from_url(document_url) or candidate_url.domain
        source_quality = assess_source_quality(
            canonical_url=document_url,
            domain=document_domain,
            parsed_metadata=parsed_content.metadata,
        )
        source_document = self.source_document_repository.get_for_task_url(
            task_id,
            document_url,
        )
        updated_existing = source_document is not None
        if source_document is None:
            source_document = self.source_document_repository.add(
                SourceDocument(
                    task_id=task_id,
                    content_snapshot_id=content_snapshot.id,
                    canonical_url=document_url,
                    domain=document_domain,
                    title=parsed_content.title or candidate_url.title,
                    source_type=parsed_content.source_type,
                    published_at=None,
                    fetched_at=content_snapshot.fetched_at,
                    authority_score=source_quality.score,
                    freshness_score=None,
                    originality_score=None,
                    consistency_score=None,
                    safety_score=None,
                    final_source_score=source_quality.score,
                )
            )
        else:
            for existing_chunk in list(source_document.chunks):
                self.session.delete(existing_chunk)
            self.session.flush()
            source_document.content_snapshot_id = content_snapshot.id
            source_document.title = parsed_content.title or candidate_url.title
            source_document.source_type = parsed_content.source_type
            source_document.fetched_at = content_snapshot.fetched_at
            source_document.domain = document_domain
            source_document.authority_score = source_quality.score
            source_document.final_source_score = source_quality.score

        parsed_chunks = chunk_text(parsed_content.text)
        for parsed_chunk in parsed_chunks:
            metadata = dict(parsed_chunk.metadata)
            chunk_quality = assess_chunk_quality(
                text=parsed_chunk.text,
                query=task.query,
                source_quality_score=source_quality.score,
                parsed_metadata=parsed_content.metadata,
            )
            metadata.update(
                {
                    "content_snapshot_id": str(content_snapshot.id),
                    "mime_type": content_snapshot.mime_type,
                    "extractor": parsed_content.metadata.get("extractor"),
                    "extractor_strategy_used": parsed_content.metadata.get(
                        "extractor_strategy_used"
                    ),
                    "fallback_used": parsed_content.metadata.get("fallback_used"),
                    "removed_boilerplate_count": parsed_content.metadata.get(
                        "removed_boilerplate_count"
                    ),
                    "extracted_text_length": parsed_content.metadata.get("extracted_text_length"),
                    "text_cleanup_applied": parsed_content.metadata.get("text_cleanup_applied"),
                    "dropped_broken_link_fragments": parsed_content.metadata.get(
                        "dropped_broken_link_fragments"
                    ),
                    "preserved_link_text_count": parsed_content.metadata.get(
                        "preserved_link_text_count"
                    ),
                    "link_text_extraction_strategy": parsed_content.metadata.get(
                        "link_text_extraction_strategy"
                    ),
                    "source_quality_score": source_quality.score,
                    "source_quality_reason": source_quality.reason,
                    "content_quality": chunk_quality.content_quality,
                    "content_quality_score": chunk_quality.content_quality_score,
                    "query_relevance_score": chunk_quality.query_relevance_score,
                    "boilerplate_score": chunk_quality.boilerplate_score,
                    "eligible_for_claims": chunk_quality.eligible_for_claims,
                    "should_generate_claims": chunk_quality.eligible_for_claims,
                    "is_navigation_noise": chunk_quality.is_navigation_noise,
                    "is_reference_section": chunk_quality.is_reference_section,
                    "is_diagram_or_config_section": chunk_quality.is_diagram_or_config_section,
                    "quality_reasons": chunk_quality.reasons,
                }
            )
            if parsed_content.metadata.get("reason") == "redirect_stub":
                metadata["reason"] = "redirect_stub"
                metadata["discovered_followup_url"] = parsed_content.metadata.get(
                    "discovered_followup_url"
                )
            self.source_chunk_repository.add(
                SourceChunk(
                    source_document_id=source_document.id,
                    chunk_no=parsed_chunk.chunk_no,
                    text=parsed_chunk.text,
                    token_count=parsed_chunk.token_count,
                    metadata_json=metadata,
                )
            )

        self.session.commit()
        self.session.refresh(source_document)
        return ParseLedgerEntry(
            content_snapshot=content_snapshot,
            source_document=source_document,
            chunks_created=len(parsed_chunks),
            status="UPDATED" if updated_existing else "CREATED",
            reason=None,
            updated_existing=updated_existing,
            decision=PARSE_DECISION_PARSED,
            body_length=len(raw_content),
        )

    def _get_task_or_raise(self, task_id: UUID) -> None:
        task = self.task_repository.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)


def create_parsing_service(
    session: Session,
    *,
    snapshot_object_store: SnapshotObjectStore,
    allowed_statuses: tuple[str, ...] = (PHASE2_ACTIVE_STATUS,),
) -> ParsingService:
    return ParsingService(
        session,
        task_repository=ResearchTaskRepository(session),
        content_snapshot_repository=ContentSnapshotRepository(session),
        source_document_repository=SourceDocumentRepository(session),
        source_chunk_repository=SourceChunkRepository(session),
        snapshot_object_store=snapshot_object_store,
        allowed_statuses=allowed_statuses,
    )


def parse_entry_diagnostic(entry: ParseLedgerEntry) -> dict[str, object]:
    content_snapshot = entry.content_snapshot
    fetch_job = content_snapshot.fetch_attempt.fetch_job
    candidate_url = fetch_job.candidate_url
    return {
        "snapshot_id": str(content_snapshot.id),
        "content_snapshot_id": str(content_snapshot.id),
        "canonical_url": candidate_url.canonical_url,
        "mime_type": content_snapshot.mime_type,
        "content_type": content_snapshot.mime_type,
        "storage_bucket": content_snapshot.storage_bucket,
        "storage_key": content_snapshot.storage_key,
        "snapshot_bytes": content_snapshot.bytes,
        "body_length": entry.body_length,
        "decision": entry.decision,
        "status": entry.status,
        "reason": entry.reason.value if entry.reason is not None else None,
        "parser_error": entry.parser_error,
        "source_document_id": (
            str(entry.source_document.id) if entry.source_document is not None else None
        ),
        "content_quality": _first_chunk_metadata(entry.source_document, "content_quality"),
        "content_quality_score": _first_chunk_metadata(
            entry.source_document,
            "content_quality_score",
        ),
        "extractor_strategy_used": _first_chunk_metadata(
            entry.source_document,
            "extractor_strategy_used",
        ),
        "fallback_used": _first_chunk_metadata(entry.source_document, "fallback_used"),
        "removed_boilerplate_count": _first_chunk_metadata(
            entry.source_document,
            "removed_boilerplate_count",
        ),
        "extracted_text_length": _first_chunk_metadata(
            entry.source_document,
            "extracted_text_length",
        ),
        "text_cleanup_applied": _first_chunk_metadata(
            entry.source_document,
            "text_cleanup_applied",
        ),
        "dropped_broken_link_fragments": _first_chunk_metadata(
            entry.source_document,
            "dropped_broken_link_fragments",
        ),
        "preserved_link_text_count": _first_chunk_metadata(
            entry.source_document,
            "preserved_link_text_count",
        ),
        "link_text_extraction_strategy": _first_chunk_metadata(
            entry.source_document,
            "link_text_extraction_strategy",
        ),
        "source_quality_score": (
            entry.source_document.final_source_score if entry.source_document is not None else None
        ),
        "reason_detail": _first_chunk_metadata(entry.source_document, "reason"),
        "discovered_followup_url": _first_chunk_metadata(
            entry.source_document,
            "discovered_followup_url",
        ),
        "chunks_created": entry.chunks_created,
    }


def _document_url_for_fetch(candidate_url: str, trace: dict[str, object] | None) -> str:
    if isinstance(trace, dict):
        final_url = trace.get("final_url")
        if isinstance(final_url, str) and final_url.startswith(("http://", "https://")):
            return final_url
    return candidate_url


def _domain_from_url(url: str) -> str | None:
    parsed = urlsplit(url)
    if parsed.hostname is None:
        return None
    return parsed.hostname.lower()


def _first_chunk_metadata(
    source_document: SourceDocument | None,
    key: str,
) -> object | None:
    if source_document is None or not source_document.chunks:
        return None
    return source_document.chunks[0].metadata_json.get(key)
