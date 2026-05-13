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
    TaskEventRepository,
)
from packages.observability import (
    get_logger,
    record_browser_fallback_attempted,
    record_browser_fallback_considered,
    record_browser_fallback_failed,
    record_browser_fallback_skipped,
    record_browser_fallback_succeeded,
    record_fetch_failure_class,
    record_fetch_results,
)
from services.orchestrator.app.acquisition import HttpAcquisitionClient
from services.orchestrator.app.acquisition.acquisition_priority import (
    any_authoritative_candidates,
    candidate_authoritative_heuristic,
    candidate_high_priority_for_success_hold,
    documentation_lane_fetch_score_delta,
    official_repository_readme_acquire_hold,
)
from services.orchestrator.app.acquisition.browser_backend import (
    BrowserFetchBackend,
    should_attempt_browser_fallback,
)
from services.orchestrator.app.acquisition.browser_fallback_diag import (
    compute_browser_fallback_diagnostics,
    finalize_browser_attempt_diagnostics,
)
from services.orchestrator.app.acquisition.fetch_outcome import finalize_static_fetch_result
from services.orchestrator.app.acquisition.http_client import HttpFetchResult
from services.orchestrator.app.research_quality import (
    classify_source_intent,
    source_intent_metadata,
)
from services.orchestrator.app.services.research_tasks import (
    PHASE2_ACTIVE_STATUS,
    TaskNotFoundError,
)
from services.orchestrator.app.storage import SnapshotObjectStore, StoredObjectRef

FETCH_MODE_HTTP = "HTTP"
FETCH_MODE_BROWSER_RENDERED = "BROWSER_RENDERED"
FETCH_STATUS_PENDING = "PENDING"
FETCH_STATUS_SUCCEEDED = "SUCCEEDED"
FETCH_STATUS_FAILED = "FAILED"

ACQUISITION_BROWSER_FALLBACK_EVENT = "acquisition.browser_fallback"
ACQUISITION_FETCH_BATCH_SUMMARY_EVENT = "acquisition.fetch_batch_summary"
TECHNICAL_EXPLANATION_ROLE_QUOTAS: tuple[tuple[str, int], ...] = (
    ("official_docs", 3),
    ("official_reference", 2),
    ("official_repository", 1),
    ("official_blog_or_changelog", 1),
    ("high_quality_secondary_reference", 1),
)
TECHNICAL_EXPLANATION_ROLE_ORDER = tuple(role for role, _ in TECHNICAL_EXPLANATION_ROLE_QUOTAS)

_OFFICIAL_REPOSITORY_README_ELEVATION_GLOBAL_CAP = 6
_OFFICIAL_REPOSITORY_README_PER_REPO_CAP = 2

logger = get_logger(__name__)


def _json_safe_browser_diag(diag: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in diag.items():
        if value is None:
            continue
        if isinstance(value, str | int | float | bool):
            out[key] = value
        else:
            out[key] = str(value)
    return out


def _record_browser_fallback_task_event(
    repo: TaskEventRepository | None,
    task_id: UUID,
    payload: dict[str, Any],
) -> None:
    if repo is None:
        return
    try:
        repo.record(
            task_id=task_id,
            event_type=ACQUISITION_BROWSER_FALLBACK_EVENT,
            payload_json=payload,
        )
    except Exception:
        logger.warning(
            "fetch.browser_fallback.task_event_failed",
            extra={"task_id": str(task_id)},
            exc_info=True,
        )


def _record_fetch_batch_summary_task_event(
    repo: TaskEventRepository | None,
    task_id: UUID,
    payload: dict[str, Any],
) -> None:
    if repo is None:
        return
    try:
        repo.record(
            task_id=task_id,
            event_type=ACQUISITION_FETCH_BATCH_SUMMARY_EVENT,
            payload_json=payload,
        )
    except Exception:
        logger.warning(
            "fetch.batch_summary.task_event_failed",
            extra={"task_id": str(task_id)},
            exc_info=True,
        )


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
    browser_fetch_job: FetchJob | None = None
    browser_fetch_attempt: FetchAttempt | None = None
    browser_content_snapshot: ContentSnapshot | None = None


@dataclass(frozen=True)
class AcquisitionBatchResult:
    task: ResearchTask
    selected_candidates_from_search: list[CandidateUrl]
    selected_candidates_for_fetch: list[CandidateUrl]
    skipped_by_triage_candidates: list[CandidateUrl]
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
        max_must_fetch_per_round: int = 3,
        allowed_statuses: tuple[str, ...] = (PHASE2_ACTIVE_STATUS,),
        browser_fetch_backend_impl: BrowserFetchBackend | None = None,
        browser_fetch_backend_setting: str = "none",
        task_event_repository: TaskEventRepository | None = None,
        min_successful_authoritative_snapshots: int = 0,
        defer_success_target_for_high_priority: bool = False,
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
        self.max_must_fetch_per_round = max(0, max_must_fetch_per_round)
        self.allowed_statuses = allowed_statuses
        self._browser_backend = browser_fetch_backend_impl
        self._browser_fetch_backend_setting = (
            (browser_fetch_backend_setting or "none").strip().lower()
        )
        self._task_event_repository = task_event_repository
        self.min_successful_authoritative_snapshots = max(
            0, int(min_successful_authoritative_snapshots)
        )
        self.defer_success_target_for_high_priority = bool(defer_success_target_for_high_priority)

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
        skipped_by_triage_candidates: list[CandidateUrl] = []
        if candidate_url_ids is not None:
            selected_candidates_for_fetch = selected_candidates_from_search
        else:
            selected_candidates_for_fetch, skipped_by_triage_candidates = (
                _sort_candidates_for_fetch(
                    selected_candidates_from_search,
                    query=task.query,
                    max_must_fetch_per_round=self.max_must_fetch_per_round,
                )
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
        authoritative_snapshots = 0
        success_target_continue_count = 0

        for candidate_url in selected_candidates_for_fetch:
            if created >= effective_limit:
                break
            if success_target is not None and successful_snapshots >= success_target:
                if self._should_defer_success_target_stop(
                    selected_candidates_for_fetch,
                    attempted_candidate_ids,
                    created,
                    effective_limit,
                    authoritative_snapshots,
                ):
                    success_target_continue_count += 1
                else:
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
                        browser_fetch_job=None,
                        browser_fetch_attempt=None,
                        browser_content_snapshot=None,
                    )
                )
                skipped_existing += 1
                if content_snapshot is not None:
                    successful_snapshots += 1
                    if candidate_authoritative_heuristic(candidate_url):
                        authoritative_snapshots += 1
                continue

            entry = self._execute_candidate_fetch(task, candidate_url)
            attempted_candidate_ids.add(candidate_url.id)
            entries.append(entry)
            created += 1
            if entry.fetch_job.status == FETCH_STATUS_SUCCEEDED or (
                entry.browser_fetch_job is not None
                and entry.browser_fetch_job.status == FETCH_STATUS_SUCCEEDED
            ):
                succeeded += 1
                if entry.content_snapshot is not None or entry.browser_content_snapshot is not None:
                    successful_snapshots += 1
                    if candidate_authoritative_heuristic(candidate_url):
                        authoritative_snapshots += 1
            else:
                failed += 1

        unattempted_candidates = [
            candidate_url
            for candidate_url in selected_candidates_for_fetch
            if candidate_url.id not in attempted_candidate_ids
        ]

        stop_reason = "no_unattempted_candidates"
        if unattempted_candidates:
            if created >= effective_limit:
                stop_reason = "fetch_budget_exhausted"
            elif success_target is not None and successful_snapshots >= success_target:
                stop_reason = "success_target_met"
            else:
                stop_reason = "unknown_early_exit"

        _record_fetch_batch_summary_task_event(
            self._task_event_repository,
            task.id,
            {
                "effective_limit": effective_limit,
                "limit_parameter": limit,
                "max_candidates_per_request": self.max_candidates_per_request,
                "max_must_fetch_per_round": self.max_must_fetch_per_round,
                "target_successful_snapshots": target_successful_snapshots,
                "authoritative_snapshots": authoritative_snapshots,
                "success_target_continue_count": success_target_continue_count,
                "min_successful_authoritative_snapshots": (
                    self.min_successful_authoritative_snapshots
                ),
                "defer_success_target_for_high_priority": (
                    self.defer_success_target_for_high_priority
                ),
                "created": created,
                "skipped_existing": skipped_existing,
                "succeeded": succeeded,
                "failed": failed,
                "selected_from_search_count": len(selected_candidates_from_search),
                "selected_for_fetch_count": len(selected_candidates_for_fetch),
                "skipped_by_triage_count": len(skipped_by_triage_candidates),
                "unattempted_count": len(unattempted_candidates),
                "stop_reason": stop_reason,
                "unattempted_candidate_ids": [str(c.id) for c in unattempted_candidates],
            },
        )

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
            skipped_by_triage_candidates=skipped_by_triage_candidates,
            unattempted_candidates=unattempted_candidates,
            entries=entries,
            created=created,
            skipped_existing=skipped_existing,
            succeeded=succeeded,
            failed=failed,
        )

    def _should_defer_success_target_stop(
        self,
        selected_candidates_for_fetch: list[CandidateUrl],
        attempted_candidate_ids: set[UUID],
        created: int,
        effective_limit: int,
        authoritative_snapshots: int,
    ) -> bool:
        if created >= effective_limit:
            return False
        unattempted = [
            c for c in selected_candidates_for_fetch if c.id not in attempted_candidate_ids
        ]
        if not unattempted:
            return False
        if self.min_successful_authoritative_snapshots > 0:
            if authoritative_snapshots < self.min_successful_authoritative_snapshots:
                if any_authoritative_candidates(unattempted):
                    return True
        if any(official_repository_readme_acquire_hold(c) for c in unattempted):
            return True
        if self.defer_success_target_for_high_priority:
            if any(candidate_high_priority_for_success_hold(c) for c in unattempted):
                return True
        return False

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

        fetch_result = finalize_static_fetch_result(
            self.http_client.fetch(candidate_url.canonical_url),
        )
        fetch_attempt.http_status = fetch_result.http_status
        fetch_attempt.error_code = fetch_result.error_code
        fetch_attempt.finished_at = datetime.now(UTC)
        fetch_attempt.trace_json = fetch_result.trace
        record_fetch_failure_class(
            code=_fetch_metrics_failure_label(
                error_code=fetch_attempt.error_code,
                trace_json=fetch_attempt.trace_json,
            ),
        )

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

        browser_fetch_job: FetchJob | None = None
        browser_fetch_attempt: FetchAttempt | None = None
        browser_content_snapshot: ContentSnapshot | None = None
        browser_stored_object_ref: StoredObjectRef | None = None

        diag = compute_browser_fallback_diagnostics(
            error_code=fetch_attempt.error_code,
            trace_json=fetch_attempt.trace_json,
            browser_fetch_backend_setting=self._browser_fetch_backend_setting,
            backend_available=self._browser_backend is not None,
        )
        if diag["browser_fallback_configured"]:
            considered_reason = (
                diag["browser_fallback_skipped_reason"]
                or diag["browser_fallback_trigger_reason"]
                or "unknown"
            )
            record_browser_fallback_considered(reason=considered_reason)

        logger.info(
            "fetch.browser_fallback",
            extra={
                "task_id": str(task.id),
                "candidate_url_id": str(candidate_url.id),
                **diag,
            },
        )

        should_run_browser = should_attempt_browser_fallback(
            error_code=fetch_attempt.error_code,
            trace_json=fetch_attempt.trace_json,
            browser_fetch_backend=self._browser_fetch_backend_setting,
        ) and (self._browser_backend is not None)

        if diag["browser_fallback_configured"] and not should_run_browser:
            if diag["browser_fallback_skipped_reason"]:
                record_browser_fallback_skipped(reason=diag["browser_fallback_skipped_reason"])

        if should_run_browser:
            backend_name = (
                self._browser_backend.name if self._browser_backend is not None else "unknown"
            )
            record_browser_fallback_attempted(backend=backend_name)
            browser_fetch_job = self.fetch_job_repository.add(
                FetchJob(
                    task_id=task.id,
                    candidate_url_id=candidate_url.id,
                    mode=FETCH_MODE_BROWSER_RENDERED,
                    status=FETCH_STATUS_PENDING,
                    scheduled_at=datetime.now(UTC),
                )
            )
            browser_fetch_attempt = self.fetch_attempt_repository.add(
                FetchAttempt(
                    fetch_job_id=browser_fetch_job.id,
                    attempt_no=1,
                    started_at=datetime.now(UTC),
                )
            )
            browser_result: HttpFetchResult
            try:
                raw_browser = self._browser_backend.fetch_rendered(
                    candidate_url.canonical_url,
                    trace_context={
                        "static_fetch_attempt_id": str(fetch_attempt.id),
                        "static_fetch_job_id": str(fetch_job.id),
                    },
                )
                browser_result = finalize_static_fetch_result(raw_browser)
            except Exception as error:
                browser_result = HttpFetchResult(
                    requested_url=candidate_url.canonical_url,
                    final_url=None,
                    http_status=None,
                    error_code="browser_fetch_failed",
                    mime_type=None,
                    content=None,
                    content_hash=None,
                    trace={
                        "acquisition_channel": "browser_playwright",
                        "exception_type": type(error).__name__,
                        "message": str(error),
                    },
                )

            browser_fetch_attempt.http_status = browser_result.http_status
            browser_fetch_attempt.error_code = browser_result.error_code
            browser_fetch_attempt.finished_at = datetime.now(UTC)
            browser_fetch_attempt.trace_json = browser_result.trace
            record_fetch_failure_class(
                code=_fetch_metrics_failure_label(
                    error_code=browser_fetch_attempt.error_code,
                    trace_json=browser_fetch_attempt.trace_json,
                ),
            )

            extracted_title = browser_result.trace.get("browser_title")
            extracted_title_str = extracted_title if isinstance(extracted_title, str) else None

            if (
                browser_result.content is not None
                and browser_result.content_hash is not None
                and browser_result.mime_type is not None
            ):
                browser_storage_key = _build_snapshot_key(
                    task.id,
                    candidate_url.id,
                    browser_fetch_attempt.id,
                    artifact_name="rendered-response.bin",
                )
                try:
                    browser_stored_object_ref = self.snapshot_object_store.put_bytes(
                        bucket=self.snapshot_bucket,
                        key=browser_storage_key,
                        content=browser_result.content,
                        content_type=browser_result.mime_type,
                    )
                except Exception as error:
                    browser_fetch_attempt.error_code = "storage_write_failed"
                    browser_fetch_attempt.trace_json = _merge_trace(
                        browser_result.trace,
                        {
                            "storage_error": {
                                "exception_type": type(error).__name__,
                                "message": str(error),
                            }
                        },
                    )
                    record_fetch_failure_class(
                        code=_fetch_metrics_failure_label(
                            error_code=browser_fetch_attempt.error_code,
                            trace_json=browser_fetch_attempt.trace_json,
                        ),
                    )
                else:
                    browser_content_snapshot = self.content_snapshot_repository.add(
                        ContentSnapshot(
                            fetch_attempt_id=browser_fetch_attempt.id,
                            storage_bucket=browser_stored_object_ref.bucket,
                            storage_key=browser_stored_object_ref.key,
                            content_hash=browser_result.content_hash,
                            mime_type=browser_result.mime_type,
                            bytes=len(browser_result.content),
                            extracted_title=extracted_title_str,
                            fetched_at=datetime.now(UTC),
                        )
                    )

            if browser_fetch_job is not None:
                browser_fetch_job.status = (
                    FETCH_STATUS_SUCCEEDED
                    if browser_fetch_attempt is not None
                    and browser_fetch_attempt.error_code is None
                    else FETCH_STATUS_FAILED
                )

            if browser_fetch_attempt.error_code is None:
                record_browser_fallback_succeeded(backend=backend_name)
            else:
                record_browser_fallback_failed(
                    backend=backend_name,
                    code=browser_fetch_attempt.error_code,
                )

        browser_result_label: str | None = None
        browser_err: str | None = None
        if should_run_browser and browser_fetch_attempt is not None:
            browser_err = browser_fetch_attempt.error_code
            browser_result_label = (
                "succeeded" if browser_fetch_attempt.error_code is None else "failed"
            )

        final_browser_diag = finalize_browser_attempt_diagnostics(
            diag,
            attempted=should_run_browser,
            result=browser_result_label,
            error_code=browser_err,
        )
        fetch_attempt.trace_json = _merge_trace(
            fetch_attempt.trace_json or {},
            {"browser_fallback": _json_safe_browser_diag(final_browser_diag)},
        )
        if diag["browser_fallback_configured"]:
            _record_browser_fallback_task_event(
                self._task_event_repository,
                task.id,
                {
                    "candidate_url_id": str(candidate_url.id),
                    "canonical_url": candidate_url.canonical_url,
                    "static_fetch_job_id": str(fetch_job.id),
                    "static_fetch_attempt_id": str(fetch_attempt.id),
                    **_json_safe_browser_diag(final_browser_diag),
                },
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
            if browser_stored_object_ref is not None:
                self.snapshot_object_store.delete_object(
                    bucket=browser_stored_object_ref.bucket,
                    key=browser_stored_object_ref.key,
                )
            raise

        self.session.refresh(fetch_job)
        self.session.refresh(fetch_attempt)
        if content_snapshot is not None:
            self.session.refresh(content_snapshot)
        if browser_fetch_job is not None:
            self.session.refresh(browser_fetch_job)
        if browser_fetch_attempt is not None:
            self.session.refresh(browser_fetch_attempt)
        if browser_content_snapshot is not None:
            self.session.refresh(browser_content_snapshot)
        return AcquisitionLedgerEntry(
            candidate_url=candidate_url,
            fetch_job=fetch_job,
            fetch_attempt=fetch_attempt,
            content_snapshot=content_snapshot,
            skipped_existing=False,
            browser_fetch_job=browser_fetch_job,
            browser_fetch_attempt=browser_fetch_attempt,
            browser_content_snapshot=browser_content_snapshot,
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
    max_must_fetch_per_round: int = 3,
    allowed_statuses: tuple[str, ...] = (PHASE2_ACTIVE_STATUS,),
    browser_fetch_backend_impl: BrowserFetchBackend | None = None,
    browser_fetch_backend_setting: str = "none",
    task_event_repository: TaskEventRepository | None = None,
    min_successful_authoritative_snapshots: int = 0,
    defer_success_target_for_high_priority: bool = False,
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
        max_must_fetch_per_round=max_must_fetch_per_round,
        allowed_statuses=allowed_statuses,
        browser_fetch_backend_impl=browser_fetch_backend_impl,
        browser_fetch_backend_setting=browser_fetch_backend_setting,
        task_event_repository=task_event_repository,
        min_successful_authoritative_snapshots=min_successful_authoritative_snapshots,
        defer_success_target_for_high_priority=defer_success_target_for_high_priority,
    )


def _build_snapshot_key(
    task_id: UUID,
    candidate_url_id: UUID,
    fetch_attempt_id: UUID,
    *,
    artifact_name: str = "response.bin",
) -> str:
    return (
        f"research-task/{task_id}/candidate-url/{candidate_url_id}/"
        f"fetch-attempt/{fetch_attempt_id}/{artifact_name}"
    )


def _merge_trace(original_trace: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    merged_trace = dict(original_trace)
    merged_trace.update(patch)
    return merged_trace


def _fetch_metrics_failure_label(
    *,
    error_code: str | None,
    trace_json: dict[str, Any] | None,
) -> str | None:
    """Prometheus label: prefer explicit error_code; else static HTML hold decision."""
    if error_code is not None:
        return error_code
    trace = trace_json if isinstance(trace_json, dict) else {}
    if trace.get("eligible_for_evidence_parse") is False:
        decision = trace.get("static_html_quality_decision")
        if isinstance(decision, str) and decision.strip():
            return decision.strip()
        return "static_html_parse_held"
    return None


def _is_official_repository_readme_derivative_candidate(candidate: CandidateUrl) -> bool:
    """Narrow: technical-explanation raw README derivatives only (not arbitrary raw files)."""
    md = candidate.metadata_json or {}
    if md.get("official_repository_readme_derivative") is not True:
        return False
    if str(md.get("source_intent") or "").strip() != "official_repository_readme":
        return False
    domain = (candidate.domain or "").lower().rstrip(".")
    if domain != "raw.githubusercontent.com":
        return False
    path = urlsplit(candidate.canonical_url).path.lower()
    return path.endswith("/readme.md") or path.endswith("/readme.markdown")


def _raw_readme_repo_identity_key(canonical_url: str) -> tuple[str, str] | None:
    parsed = urlsplit(canonical_url)
    if parsed.netloc.lower().rstrip(".") != "raw.githubusercontent.com":
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 4:
        return None
    if parts[-1].lower() not in {"readme.md", "readme.markdown"}:
        return None
    return (parts[0].lower(), parts[1].lower())


def _elevate_official_repository_readme_candidates_for_technical_explanation(
    candidates: list[CandidateUrl],
    *,
    query: str | None,
    max_must_fetch_per_round: int = 3,
) -> list[CandidateUrl]:
    """
    Move bounded raw README derivatives early for technical-explanation tasks.

    Does not apply to GitHub issues/PRs, arbitrary raw paths, or non-derivative rows.

    Only the first ``max_must_fetch_per_round`` must_fetch rows are pinned ahead of README
    derivatives so a long tail of triaged must_fetch docs cannot push raw README URLs past
    per-round fetch attempt budgets.
    """
    if query is None or not _query_asks_technical_explanation(query):
        return candidates

    must_fetch_all = [c for c in candidates if _active_triage_decision(c) == "must_fetch"]
    cap = max(0, max_must_fetch_per_round)
    must_prefix = must_fetch_all[:cap]
    must_prefix_ids = {c.id for c in must_prefix}

    readme_pool = [
        c
        for c in candidates
        if c.id not in must_prefix_ids and _is_official_repository_readme_derivative_candidate(c)
    ]
    readme_pool.sort(key=lambda c: _fetch_priority_key(c, query=query))

    per_repo: dict[tuple[str, str], int] = {}
    elevated: list[CandidateUrl] = []
    elevated_ids: set[UUID] = set()
    for c in readme_pool:
        if len(elevated) >= _OFFICIAL_REPOSITORY_README_ELEVATION_GLOBAL_CAP:
            break
        key = _raw_readme_repo_identity_key(c.canonical_url)
        if key is None:
            continue
        if per_repo.get(key, 0) >= _OFFICIAL_REPOSITORY_README_PER_REPO_CAP:
            continue
        per_repo[key] = per_repo.get(key, 0) + 1
        elevated.append(c)
        elevated_ids.add(c.id)

    tail = [c for c in candidates if c.id not in must_prefix_ids and c.id not in elevated_ids]
    return must_prefix + elevated + tail


def _sort_candidates_for_fetch(
    candidates: list[CandidateUrl],
    *,
    query: str | None = None,
    max_must_fetch_per_round: int = 3,
) -> tuple[list[CandidateUrl], list[CandidateUrl]]:
    fetchable_candidates: list[CandidateUrl] = []
    skipped_by_triage: list[CandidateUrl] = []
    for candidate in candidates:
        triage_decision = _active_triage_decision(candidate)
        if triage_decision in {"skip_duplicate", "skip_low_value", "skip_unsafe_or_invalid"}:
            skipped_by_triage.append(candidate)
            continue
        fetchable_candidates.append(candidate)
    sorted_candidates = sorted(
        fetchable_candidates, key=lambda candidate: _fetch_priority_key(candidate, query=query)
    )
    sorted_candidates = _prioritize_must_fetch_candidates(
        sorted_candidates,
        max_must_fetch_per_round=max_must_fetch_per_round,
    )
    if _query_asks_comparison(query):
        return _interleave_candidates_by_known_entity(sorted_candidates), skipped_by_triage
    if _query_asks_technical_explanation(query):
        balanced = _balance_technical_explanation_candidates_by_role(
            sorted_candidates,
            query=query,
            max_must_fetch_per_round=max_must_fetch_per_round,
        )
        return (
            _elevate_official_repository_readme_candidates_for_technical_explanation(
                balanced,
                query=query,
                max_must_fetch_per_round=max_must_fetch_per_round,
            ),
            skipped_by_triage,
        )
    return sorted_candidates, skipped_by_triage


def _prioritize_must_fetch_candidates(
    candidates: list[CandidateUrl],
    *,
    max_must_fetch_per_round: int,
) -> list[CandidateUrl]:
    if max_must_fetch_per_round <= 0:
        return candidates
    must_fetch = [
        candidate for candidate in candidates if _active_triage_decision(candidate) == "must_fetch"
    ]
    if not must_fetch:
        return candidates
    must_fetch_ids = {candidate.id for candidate in must_fetch[:max_must_fetch_per_round]}
    prioritized = [candidate for candidate in candidates if candidate.id in must_fetch_ids]
    remainder = [candidate for candidate in candidates if candidate.id not in must_fetch_ids]
    return prioritized + remainder


def _query_asks_comparison(query: str | None) -> bool:
    if query is None:
        return False
    lower = f" {query.lower()} "
    return " compare " in lower or " vs " in lower or " versus " in lower


def _query_asks_technical_explanation(query: str | None) -> bool:
    if query is None:
        return False
    lower = query.lower()
    if any(term in lower for term in ("deploy", "deployment", "docker", "install")):
        return False
    if "what is" in lower and "how" in lower and "work" in lower:
        return True
    if "what are" in lower and "how" in lower and "work" in lower:
        return True
    if "technical explanation" in lower or "execution model" in lower:
        return True
    return lower.startswith("explain ") and ("how" in lower or "architecture" in lower)


def _balance_technical_explanation_candidates_by_role(
    candidates: list[CandidateUrl],
    *,
    query: str | None,
    max_must_fetch_per_round: int,
) -> list[CandidateUrl]:
    if not candidates:
        return []

    selected: list[CandidateUrl] = []
    selected_ids: set[UUID] = set()

    def add(candidate: CandidateUrl) -> bool:
        if candidate.id in selected_ids:
            return False
        selected.append(candidate)
        selected_ids.add(candidate.id)
        return True

    must_fetch_candidates = [
        candidate for candidate in candidates if _active_triage_decision(candidate) == "must_fetch"
    ][: max(0, max_must_fetch_per_round)]
    for candidate in must_fetch_candidates:
        add(candidate)

    grouped: dict[str, list[CandidateUrl]] = {}
    overflow: list[CandidateUrl] = []
    for candidate in candidates:
        if candidate.id in selected_ids:
            continue
        role = _candidate_source_role(candidate, query=query)
        if role in {"forum_social_video", "low_quality_or_blocked"}:
            overflow.append(candidate)
            continue
        grouped.setdefault(role, []).append(candidate)

    for role, quota in TECHNICAL_EXPLANATION_ROLE_QUOTAS:
        for candidate in grouped.get(role, [])[:quota]:
            add(candidate)

    for role in (
        "official_docs",
        "official_reference",
        "official_repository",
        "official_blog_or_changelog",
    ):
        for candidate in grouped.get(role, []):
            add(candidate)

    if _query_allows_academic_role(query):
        for candidate in grouped.get("academic_or_standard", [])[:1]:
            add(candidate)

    for role in ("high_quality_secondary_reference", "secondary_reference", "generic_article"):
        for candidate in grouped.get(role, []):
            add(candidate)

    for role, role_candidates in grouped.items():
        if role in {
            *TECHNICAL_EXPLANATION_ROLE_ORDER,
            "academic_or_standard",
            "secondary_reference",
            "generic_article",
        }:
            continue
        for candidate in role_candidates:
            add(candidate)

    for candidate in overflow:
        add(candidate)

    return selected


def _candidate_source_role(candidate: CandidateUrl, *, query: str | None) -> str:
    metadata = fetch_priority_metadata(candidate, query=query)
    role = metadata.get("source_role")
    if isinstance(role, str) and role.strip():
        return role.strip()
    source_intent = metadata.get("source_intent")
    if isinstance(source_intent, str) and source_intent.strip():
        return source_intent.strip()
    return "generic_article"


def _query_allows_academic_role(query: str | None) -> bool:
    if query is None:
        return False
    lower = query.lower()
    return any(
        term in lower
        for term in ("academic", "paper", "research", "standard", "specification", "benchmark")
    )


def _interleave_candidates_by_known_entity(candidates: list[CandidateUrl]) -> list[CandidateUrl]:
    entity_groups: dict[str, list[CandidateUrl]] = {}
    passthrough: list[CandidateUrl] = []
    for candidate in candidates:
        metadata = candidate.metadata_json or {}
        entity = metadata.get("known_source_entity")
        if not isinstance(entity, str) or not entity.strip():
            passthrough.append(candidate)
            continue
        entity_key = entity.strip().lower()
        entity_groups.setdefault(entity_key, []).append(candidate)

    if len(entity_groups) < 2:
        return candidates

    interleaved: list[CandidateUrl] = []
    group_order = sorted(
        entity_groups,
        key=lambda key: _fetch_priority_key(entity_groups[key][0], query=None),
    )
    while any(entity_groups.values()):
        for entity_key in group_order:
            group = entity_groups[entity_key]
            if group:
                interleaved.append(group.pop(0))

    passthrough_by_id = {candidate.id: candidate for candidate in passthrough}
    known_ids = {candidate.id for candidate in interleaved}
    merged: list[CandidateUrl] = []
    interleaved_index = 0
    for candidate in candidates:
        if candidate.id in known_ids:
            if interleaved_index < len(interleaved):
                merged.append(interleaved[interleaved_index])
                interleaved_index += 1
            continue
        passthrough_candidate = passthrough_by_id.get(candidate.id)
        if passthrough_candidate is not None:
            merged.append(passthrough_candidate)
    return merged


def _fetch_priority_key(
    candidate_url: CandidateUrl,
    *,
    query: str | None = None,
) -> tuple[int, int, str]:
    return (
        _fetch_priority_score(candidate_url, query=query),
        candidate_url.rank,
        str(candidate_url.id),
    )


def _fetch_priority_score(candidate_url: CandidateUrl, *, query: str | None = None) -> int:
    triage_decision = _active_triage_decision(candidate_url)
    if triage_decision == "must_fetch":
        priority = _active_triage_fetch_priority(candidate_url)
        return priority if priority is not None else 0
    if triage_decision == "fetch_if_budget_allows":
        priority = _active_triage_fetch_priority(candidate_url)
        base = priority if priority is not None else 20
        return max(0, base)
    if triage_decision == "defer":
        priority = _active_triage_fetch_priority(candidate_url)
        base = priority if priority is not None else 70
        return max(0, base)
    domain = (candidate_url.domain or "").lower().rstrip(".")
    doc_delta = 0
    if not domain.startswith("docs.") and not domain.endswith("readthedocs.io"):
        doc_delta = documentation_lane_fetch_score_delta(candidate_url)
    score = fetch_priority_metadata(candidate_url, query=query).get("fetch_priority_score")
    base_score = score if isinstance(score, int) else 0
    metadata = candidate_url.metadata_json or {}
    adjustment = metadata.get("llm_source_judge_priority_delta")
    if isinstance(adjustment, int | float):
        return max(0, min(120, int(round(base_score + float(adjustment) + doc_delta))))
    return max(0, base_score + doc_delta)


def fetch_priority_metadata(
    candidate_url: CandidateUrl,
    *,
    query: str | None = None,
) -> dict[str, Any]:
    known_path_candidate = bool((candidate_url.metadata_json or {}).get("known_path_candidate"))
    source_metadata = source_intent_metadata(
        canonical_url=candidate_url.canonical_url,
        domain=candidate_url.domain,
        title=candidate_url.title,
        query=query,
        known_path_candidate=known_path_candidate,
    )
    metadata = candidate_url.metadata_json or {}
    override_intent = metadata.get("source_intent")
    if isinstance(override_intent, str) and override_intent.strip():
        source_metadata["source_intent"] = override_intent.strip()
    adjustment = metadata.get("llm_source_judge_priority_delta")
    if isinstance(adjustment, int | float):
        source_metadata["llm_source_judge_priority_delta"] = adjustment
        source_metadata["llm_source_judge"] = metadata.get("llm_source_judge")
    triage = _active_triage_payload(candidate_url)
    if triage:
        source_metadata.update(triage)
    return source_metadata


def _active_triage_decision(candidate_url: CandidateUrl) -> str | None:
    payload = _active_triage_payload(candidate_url)
    decision = payload.get("triage_decision") if payload else None
    return decision if isinstance(decision, str) else None


def _active_triage_fetch_priority(candidate_url: CandidateUrl) -> int | None:
    payload = _active_triage_payload(candidate_url)
    priority = payload.get("fetch_priority") if payload else None
    return priority if isinstance(priority, int) else None


def _active_triage_payload(candidate_url: CandidateUrl) -> dict[str, Any]:
    metadata = candidate_url.metadata_json or {}
    if metadata.get("llm_source_triage_active") is not True:
        return {}
    source_judge = metadata.get("llm_source_judge")
    if not isinstance(source_judge, dict):
        return {}
    output = source_judge.get("output_judgment")
    if not isinstance(output, dict):
        return {}
    decision = output.get("triage_decision")
    if not isinstance(decision, str):
        return {}
    payload: dict[str, Any] = {
        "triage_decision": decision,
        "llm_source_triage_active": True,
    }
    for key in (
        "topic_fit",
        "authority",
        "novelty",
        "expected_covered_slots",
        "source_role",
        "fetch_priority",
        "risk_flags",
    ):
        value = output.get(key)
        if value is not None:
            payload[key] = value
    return payload


def _source_category(candidate_url: CandidateUrl) -> str:
    return classify_source_intent(
        canonical_url=candidate_url.canonical_url,
        domain=candidate_url.domain,
        title=candidate_url.title,
    ).source_category
