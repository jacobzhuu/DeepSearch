from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy.orm import Session

from packages.db.models import CandidateUrl, ContentSnapshot, FetchAttempt, FetchJob, ResearchTask
from packages.db.repositories import (
    CandidateUrlRepository,
    ContentSnapshotRepository,
    FetchAttemptRepository,
    FetchJobRepository,
    ResearchTaskRepository,
)
from packages.observability import get_logger, record_fetch_results
from services.orchestrator.app.acquisition import HttpAcquisitionClient
from services.orchestrator.app.services.research_tasks import (
    PHASE2_ACTIVE_STATUS,
    TaskNotFoundError,
)
from services.orchestrator.app.storage import SnapshotObjectStore, StoredObjectRef

FETCH_MODE_HTTP = "HTTP"
FETCH_STATUS_PENDING = "PENDING"
FETCH_STATUS_SUCCEEDED = "SUCCEEDED"
FETCH_STATUS_FAILED = "FAILED"

logger = get_logger(__name__)


class AcquisitionConflictError(Exception):
    def __init__(self, task_id: UUID, current_status: str) -> None:
        super().__init__(
            f"cannot acquire candidates for task {task_id} from status {current_status}"
        )
        self.task_id = task_id
        self.current_status = current_status


class CandidateUrlNotFoundError(Exception):
    def __init__(self, task_id: UUID, candidate_url_id: UUID) -> None:
        super().__init__(f"candidate_url {candidate_url_id} was not found for task {task_id}")
        self.task_id = task_id
        self.candidate_url_id = candidate_url_id


@dataclass(frozen=True)
class AcquisitionLedgerEntry:
    candidate_url: CandidateUrl
    fetch_job: FetchJob
    fetch_attempt: FetchAttempt | None
    content_snapshot: ContentSnapshot | None
    skipped_existing: bool


@dataclass(frozen=True)
class AcquisitionBatchResult:
    task: ResearchTask
    selected_candidates_from_search: list[CandidateUrl]
    selected_candidates_for_fetch: list[CandidateUrl]
    unattempted_candidates: list[CandidateUrl]
    entries: list[AcquisitionLedgerEntry]
    created: int
    skipped_existing: int
    succeeded: int
    failed: int


@dataclass(frozen=True)
class FetchJobLedgerEntry:
    fetch_job: FetchJob
    latest_attempt: FetchAttempt | None
    content_snapshot: ContentSnapshot | None


class AcquisitionService:
    def __init__(
        self,
        session: Session,
        *,
        task_repository: ResearchTaskRepository,
        candidate_url_repository: CandidateUrlRepository,
        fetch_job_repository: FetchJobRepository,
        fetch_attempt_repository: FetchAttemptRepository,
        content_snapshot_repository: ContentSnapshotRepository,
        http_client: HttpAcquisitionClient,
        snapshot_object_store: SnapshotObjectStore,
        snapshot_bucket: str,
        max_candidates_per_request: int,
        allowed_statuses: tuple[str, ...] = (PHASE2_ACTIVE_STATUS,),
    ) -> None:
        self.session = session
        self.task_repository = task_repository
        self.candidate_url_repository = candidate_url_repository
        self.fetch_job_repository = fetch_job_repository
        self.fetch_attempt_repository = fetch_attempt_repository
        self.content_snapshot_repository = content_snapshot_repository
        self.http_client = http_client
        self.snapshot_object_store = snapshot_object_store
        self.snapshot_bucket = snapshot_bucket
        self.max_candidates_per_request = max_candidates_per_request
        self.allowed_statuses = allowed_statuses

    def acquire_candidates(
        self,
        task_id: UUID,
        *,
        candidate_url_ids: list[UUID] | None,
        limit: int | None,
        target_successful_snapshots: int | None = None,
    ) -> AcquisitionBatchResult:
        task = self._get_task(task_id)
        if task.status not in self.allowed_statuses:
            raise AcquisitionConflictError(task.id, task.status)

        effective_limit = self.max_candidates_per_request
        if limit is not None:
            effective_limit = min(limit, self.max_candidates_per_request)

        selected_candidates_from_search = self._select_candidates(
            task.id,
            candidate_url_ids=candidate_url_ids,
        )
        selected_candidates_for_fetch = (
            selected_candidates_from_search
            if candidate_url_ids is not None
            else _sort_candidates_for_fetch(selected_candidates_from_search)
        )
        success_target = (
            max(target_successful_snapshots, 1) if target_successful_snapshots is not None else None
        )

        entries: list[AcquisitionLedgerEntry] = []
        attempted_candidate_ids: set[UUID] = set()
        created = 0
        skipped_existing = 0
        succeeded = 0
        failed = 0
        successful_snapshots = 0

        for candidate_url in selected_candidates_for_fetch:
            if created >= effective_limit:
                break
            if success_target is not None and successful_snapshots >= success_target:
                break
            existing_job = self.fetch_job_repository.get_for_candidate_mode(
                candidate_url.id,
                FETCH_MODE_HTTP,
            )
            if existing_job is not None:
                attempted_candidate_ids.add(candidate_url.id)
                latest_attempt = self.fetch_attempt_repository.get_latest_for_job(existing_job.id)
                content_snapshot = None
                if latest_attempt is not None:
                    content_snapshot = self.content_snapshot_repository.get_for_fetch_attempt(
                        latest_attempt.id
                    )
                entries.append(
                    AcquisitionLedgerEntry(
                        candidate_url=candidate_url,
                        fetch_job=existing_job,
                        fetch_attempt=latest_attempt,
                        content_snapshot=content_snapshot,
                        skipped_existing=True,
                    )
                )
                skipped_existing += 1
                if content_snapshot is not None:
                    successful_snapshots += 1
                continue

            entry = self._execute_candidate_fetch(task, candidate_url)
            attempted_candidate_ids.add(candidate_url.id)
            entries.append(entry)
            created += 1
            if entry.fetch_job.status == FETCH_STATUS_SUCCEEDED:
                succeeded += 1
                if entry.content_snapshot is not None:
                    successful_snapshots += 1
            else:
                failed += 1

        unattempted_candidates = [
            candidate_url
            for candidate_url in selected_candidates_for_fetch
            if candidate_url.id not in attempted_candidate_ids
        ]

        record_fetch_results(
            created=created,
            skipped_existing=skipped_existing,
            succeeded=succeeded,
            failed=failed,
        )
        logger.info(
            "fetch.batch.completed",
            extra={
                "task_id": str(task.id),
                "created_count": created,
                "skipped_existing": skipped_existing,
                "succeeded": succeeded,
                "failed": failed,
            },
        )
        return AcquisitionBatchResult(
            task=task,
            selected_candidates_from_search=selected_candidates_from_search,
            selected_candidates_for_fetch=selected_candidates_for_fetch,
            unattempted_candidates=unattempted_candidates,
            entries=entries,
            created=created,
            skipped_existing=skipped_existing,
            succeeded=succeeded,
            failed=failed,
        )

    def list_fetch_jobs(
        self,
        task_id: UUID,
        *,
        status: str | None = None,
        limit: int | None = None,
    ) -> list[FetchJobLedgerEntry]:
        self._get_task(task_id)
        fetch_jobs = self.fetch_job_repository.list_for_task(task_id, status=status, limit=limit)
        ledger_entries: list[FetchJobLedgerEntry] = []
        for fetch_job in fetch_jobs:
            latest_attempt = self.fetch_attempt_repository.get_latest_for_job(fetch_job.id)
            content_snapshot = None
            if latest_attempt is not None:
                content_snapshot = self.content_snapshot_repository.get_for_fetch_attempt(
                    latest_attempt.id
                )
            ledger_entries.append(
                FetchJobLedgerEntry(
                    fetch_job=fetch_job,
                    latest_attempt=latest_attempt,
                    content_snapshot=content_snapshot,
                )
            )
        return ledger_entries

    def list_fetch_attempts(
        self,
        task_id: UUID,
        *,
        fetch_job_id: UUID | None = None,
        limit: int | None = None,
    ) -> list[FetchAttempt]:
        self._get_task(task_id)
        return self.fetch_attempt_repository.list_for_task(
            task_id,
            fetch_job_id=fetch_job_id,
            limit=limit,
        )

    def list_content_snapshots(
        self,
        task_id: UUID,
        *,
        limit: int | None = None,
    ) -> list[ContentSnapshot]:
        self._get_task(task_id)
        return self.content_snapshot_repository.list_for_task(task_id, limit=limit)

    def _execute_candidate_fetch(
        self,
        task: ResearchTask,
        candidate_url: CandidateUrl,
    ) -> AcquisitionLedgerEntry:
        fetch_job = self.fetch_job_repository.add(
            FetchJob(
                task_id=task.id,
                candidate_url_id=candidate_url.id,
                mode=FETCH_MODE_HTTP,
                status=FETCH_STATUS_PENDING,
                scheduled_at=datetime.now(UTC),
            )
        )
        fetch_attempt = self.fetch_attempt_repository.add(
            FetchAttempt(
                fetch_job_id=fetch_job.id,
                attempt_no=1,
                started_at=datetime.now(UTC),
            )
        )

        fetch_result = self.http_client.fetch(candidate_url.canonical_url)
        fetch_attempt.http_status = fetch_result.http_status
        fetch_attempt.error_code = fetch_result.error_code
        fetch_attempt.finished_at = datetime.now(UTC)
        fetch_attempt.trace_json = fetch_result.trace

        stored_object_ref: StoredObjectRef | None = None
        content_snapshot: ContentSnapshot | None = None
        if (
            fetch_result.content is not None
            and fetch_result.content_hash is not None
            and fetch_result.mime_type is not None
        ):
            storage_key = _build_snapshot_key(task.id, candidate_url.id, fetch_attempt.id)
            try:
                stored_object_ref = self.snapshot_object_store.put_bytes(
                    bucket=self.snapshot_bucket,
                    key=storage_key,
                    content=fetch_result.content,
                    content_type=fetch_result.mime_type,
                )
            except Exception as error:
                fetch_attempt.error_code = "storage_write_failed"
                fetch_attempt.trace_json = _merge_trace(
                    fetch_result.trace,
                    {
                        "storage_error": {
                            "exception_type": type(error).__name__,
                            "message": str(error),
                        }
                    },
                )
            else:
                content_snapshot = self.content_snapshot_repository.add(
                    ContentSnapshot(
                        fetch_attempt_id=fetch_attempt.id,
                        storage_bucket=stored_object_ref.bucket,
                        storage_key=stored_object_ref.key,
                        content_hash=fetch_result.content_hash,
                        mime_type=fetch_result.mime_type,
                        bytes=len(fetch_result.content),
                        extracted_title=None,
                        fetched_at=datetime.now(UTC),
                    )
                )

        fetch_job.status = (
            FETCH_STATUS_SUCCEEDED if fetch_attempt.error_code is None else FETCH_STATUS_FAILED
        )

        try:
            self.session.commit()
        except Exception:
            self.session.rollback()
            if stored_object_ref is not None:
                self.snapshot_object_store.delete_object(
                    bucket=stored_object_ref.bucket,
                    key=stored_object_ref.key,
                )
            raise

        self.session.refresh(fetch_job)
        self.session.refresh(fetch_attempt)
        if content_snapshot is not None:
            self.session.refresh(content_snapshot)
        return AcquisitionLedgerEntry(
            candidate_url=candidate_url,
            fetch_job=fetch_job,
            fetch_attempt=fetch_attempt,
            content_snapshot=content_snapshot,
            skipped_existing=False,
        )

    def _get_task(self, task_id: UUID) -> ResearchTask:
        task = self.task_repository.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def _select_candidates(
        self,
        task_id: UUID,
        *,
        candidate_url_ids: list[UUID] | None,
    ) -> list[CandidateUrl]:
        task_candidates = self.candidate_url_repository.list_for_task(task_id)
        candidates_by_id = {candidate.id: candidate for candidate in task_candidates}

        if candidate_url_ids is not None:
            selected_candidates: list[CandidateUrl] = []
            seen_candidate_ids: set[UUID] = set()
            for candidate_url_id in candidate_url_ids:
                if candidate_url_id in seen_candidate_ids:
                    continue
                candidate = candidates_by_id.get(candidate_url_id)
                if candidate is None:
                    raise CandidateUrlNotFoundError(task_id, candidate_url_id)
                selected_candidates.append(candidate)
                seen_candidate_ids.add(candidate_url_id)
            return selected_candidates

        return task_candidates


def create_acquisition_service(
    session: Session,
    *,
    http_client: HttpAcquisitionClient,
    snapshot_object_store: SnapshotObjectStore,
    snapshot_bucket: str,
    max_candidates_per_request: int,
    allowed_statuses: tuple[str, ...] = (PHASE2_ACTIVE_STATUS,),
) -> AcquisitionService:
    return AcquisitionService(
        session,
        task_repository=ResearchTaskRepository(session),
        candidate_url_repository=CandidateUrlRepository(session),
        fetch_job_repository=FetchJobRepository(session),
        fetch_attempt_repository=FetchAttemptRepository(session),
        content_snapshot_repository=ContentSnapshotRepository(session),
        http_client=http_client,
        snapshot_object_store=snapshot_object_store,
        snapshot_bucket=snapshot_bucket,
        max_candidates_per_request=max_candidates_per_request,
        allowed_statuses=allowed_statuses,
    )


def _build_snapshot_key(task_id: UUID, candidate_url_id: UUID, fetch_attempt_id: UUID) -> str:
    return (
        f"research-task/{task_id}/candidate-url/{candidate_url_id}/"
        f"fetch-attempt/{fetch_attempt_id}/response.bin"
    )


def _merge_trace(original_trace: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged_trace = dict(original_trace)
    merged_trace.update(patch)
    return merged_trace


def _sort_candidates_for_fetch(candidates: list[CandidateUrl]) -> list[CandidateUrl]:
    return sorted(candidates, key=_fetch_priority_key)


def _fetch_priority_key(candidate_url: CandidateUrl) -> tuple[int, int, str]:
    return (
        _fetch_priority_score(candidate_url),
        candidate_url.rank,
        str(candidate_url.id),
    )


def _fetch_priority_score(candidate_url: CandidateUrl) -> int:
    domain = (candidate_url.domain or "").strip().lower().removeprefix("www.")
    parsed = urlsplit(candidate_url.canonical_url)
    path = parsed.path.strip().lower()
    title = (candidate_url.title or "").strip().lower()

    if _is_social_video_or_forum_domain(domain):
        return 90
    if domain == "github.com":
        return 80
    if _is_docs_like(domain=domain, path=path, title=title):
        return 0
    if _is_project_homepage(domain=domain, path=path):
        return 10
    if domain.endswith("wikipedia.org"):
        return 20
    return 50


def _is_docs_like(*, domain: str, path: str, title: str) -> bool:
    if domain.startswith("docs.") or domain.startswith("documentation."):
        return True
    docs_markers = (
        "/docs",
        "/doc/",
        "/documentation",
        "/guide",
        "/guides",
        "/manual",
        "/reference",
    )
    if any(marker in path for marker in docs_markers):
        return True
    return "documentation" in title or "docs" in title


def _is_project_homepage(*, domain: str, path: str) -> bool:
    if not domain or domain.endswith("wikipedia.org"):
        return False
    normalized_path = path.rstrip("/")
    return normalized_path in {"", "/"}


def _is_social_video_or_forum_domain(domain: str) -> bool:
    social_video_forum_domains = (
        "reddit.com",
        "youtube.com",
        "youtu.be",
        "x.com",
        "twitter.com",
        "facebook.com",
        "instagram.com",
        "tiktok.com",
        "medium.com",
        "news.ycombinator.com",
        "stackoverflow.com",
        "stackexchange.com",
        "quora.com",
    )
    return any(domain == item or domain.endswith(f".{item}") for item in social_video_forum_domains)
