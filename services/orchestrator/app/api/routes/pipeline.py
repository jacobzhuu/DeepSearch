from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from services.orchestrator.app.acquisition import HttpAcquisitionClient
from services.orchestrator.app.api.routes.acquisition import (
    get_http_acquisition_client,
    get_snapshot_object_store,
)
from services.orchestrator.app.api.routes.claims import get_claim_chunk_index_backend
from services.orchestrator.app.api.routes.indexing import get_chunk_index_backend
from services.orchestrator.app.api.routes.search_discovery import (
    get_query_expansion_strategy,
    get_search_provider,
)
from services.orchestrator.app.api.schemas.pipeline import (
    PipelineCountsResponse,
    PipelineFailureResponse,
    PipelineRunResponse,
)
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.indexing import ChunkIndexBackend
from services.orchestrator.app.search import QueryExpansionStrategy, SearchProvider
from services.orchestrator.app.services.acquisition import create_acquisition_service
from services.orchestrator.app.services.claims import create_claim_drafting_service
from services.orchestrator.app.services.debug_pipeline import (
    ACQUISITION_ALLOWED_STATUSES,
    DRAFT_ALLOWED_STATUSES,
    INDEXING_ALLOWED_STATUSES,
    PARSING_ALLOWED_STATUSES,
    SEARCH_ALLOWED_STATUSES,
    VERIFY_ALLOWED_STATUSES,
    DebugPipelinePreconditionError,
    DebugRealPipelineRunner,
)
from services.orchestrator.app.services.indexing import create_indexing_service
from services.orchestrator.app.services.parsing import create_parsing_service
from services.orchestrator.app.services.reporting import create_report_synthesis_service
from services.orchestrator.app.services.research_tasks import TaskNotFoundError
from services.orchestrator.app.services.search_discovery import create_search_discovery_service
from services.orchestrator.app.settings import get_settings
from services.orchestrator.app.storage import SnapshotObjectStore

router = APIRouter(prefix="/api/v1/research/tasks", tags=["pipeline"])
SessionDep = Annotated[Session, Depends(get_db_session)]


@router.post(
    "/{task_id}/run",
    response_model=PipelineRunResponse,
    status_code=status.HTTP_200_OK,
)
def run_deepsearch_pipeline(
    task_id: UUID,
    session: SessionDep,
    search_provider: Annotated[SearchProvider, Depends(get_search_provider)],
    query_expansion_strategy: Annotated[
        QueryExpansionStrategy,
        Depends(get_query_expansion_strategy),
    ],
    http_client: Annotated[HttpAcquisitionClient, Depends(get_http_acquisition_client)],
    snapshot_object_store: Annotated[SnapshotObjectStore, Depends(get_snapshot_object_store)],
    index_backend: Annotated[ChunkIndexBackend, Depends(get_chunk_index_backend)],
    claim_index_backend: Annotated[ChunkIndexBackend, Depends(get_claim_chunk_index_backend)],
) -> PipelineRunResponse:
    settings = get_settings()
    dependencies = _dependency_summary(settings)
    _validate_required_configuration(dependencies)

    runner = DebugRealPipelineRunner(
        session,
        search_service=create_search_discovery_service(
            session,
            search_provider=search_provider,
            query_expansion_strategy=query_expansion_strategy,
            max_results_per_query=min(settings.search_max_results_per_query, 5),
            allowed_statuses=SEARCH_ALLOWED_STATUSES,
        ),
        acquisition_service=create_acquisition_service(
            session,
            http_client=http_client,
            snapshot_object_store=snapshot_object_store,
            snapshot_bucket=settings.snapshot_storage_bucket,
            max_candidates_per_request=settings.acquisition_max_candidates_per_request,
            allowed_statuses=ACQUISITION_ALLOWED_STATUSES,
        ),
        parsing_service=create_parsing_service(
            session,
            snapshot_object_store=snapshot_object_store,
            allowed_statuses=PARSING_ALLOWED_STATUSES,
        ),
        indexing_service=create_indexing_service(
            session,
            index_backend=index_backend,
            indexing_max_chunks_per_request=min(settings.indexing_max_chunks_per_request, 10),
            retrieval_max_results_per_request=settings.retrieval_max_results_per_request,
            allowed_statuses=INDEXING_ALLOWED_STATUSES,
        ),
        claims_service=create_claim_drafting_service(
            session,
            index_backend=claim_index_backend,
            max_candidates_per_request=min(settings.claim_drafting_max_candidates_per_request, 5),
            verification_max_claims_per_request=min(
                settings.claim_verification_max_claims_per_request,
                5,
            ),
            retrieval_max_results_per_request=settings.retrieval_max_results_per_request,
            draft_allowed_statuses=DRAFT_ALLOWED_STATUSES,
            verify_allowed_statuses=VERIFY_ALLOWED_STATUSES,
        ),
        reporting_service=create_report_synthesis_service(
            session,
            object_store=snapshot_object_store,
            report_storage_bucket=settings.report_storage_bucket,
        ),
        dependencies=dependencies,
        fetch_limit=settings.acquisition_max_candidates_per_request,
        parse_limit=3,
        index_limit=10,
        claim_limit=5,
        target_successful_snapshots=settings.acquisition_target_successful_snapshots,
    )

    try:
        result = runner.run(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except DebugPipelinePreconditionError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "failed_stage": "PRECONDITION",
                "reason": "pipeline_precondition_failed",
                "message": str(error),
                "next_action": "Create or revise a task so it is in PLANNED before running.",
            },
        ) from error

    return PipelineRunResponse(
        task_id=result.task.id,
        status=result.task.status,
        completed=result.completed,
        running_mode=_running_mode(dependencies),
        stages_completed=result.stages_completed,
        counts=_serialize_counts(result.counts),
        report_artifact_id=result.report_artifact_id,
        report_version=result.report_version,
        report_markdown_preview=result.report_markdown_preview,
        failure=(
            PipelineFailureResponse(
                failed_stage=result.failure.stage,
                reason=result.failure.reason,
                exception=result.failure.exception,
                message=result.failure.message,
                next_action=result.failure.next_action,
                counts=_serialize_counts(result.failure.counts),
                details=result.failure.details,
            )
            if result.failure is not None
            else None
        ),
        dependencies=result.dependencies,
    )


def _dependency_summary(settings: Any) -> dict[str, Any]:
    search_mode = settings.search_provider.strip().lower()
    index_mode = settings.index_backend.strip().lower()
    return {
        "search_provider": search_mode,
        "search_mode": "smoke-search" if search_mode == "smoke" else "real-search",
        "searxng_base_url": settings.searxng_base_url,
        "snapshot_storage_backend": settings.snapshot_storage_backend,
        "snapshot_storage_root": settings.snapshot_storage_root,
        "snapshot_storage_bucket": settings.snapshot_storage_bucket,
        "report_storage_bucket": settings.report_storage_bucket,
        "index_backend": index_mode,
        "index_mode": "deterministic-local" if index_mode in {"local", "memory"} else index_mode,
        "opensearch_base_url": settings.opensearch_base_url,
        "opensearch_index_name": settings.opensearch_index_name,
        "uses_llm_api": False,
        "llm_mode": "no-LLM",
        "uses_worker_or_queue": False,
    }


def _validate_required_configuration(dependencies: dict[str, Any]) -> None:
    missing = []
    if dependencies["search_provider"] == "searxng" and not dependencies["searxng_base_url"]:
        missing.append("SEARXNG_BASE_URL")
    if not dependencies["snapshot_storage_backend"]:
        missing.append("SNAPSHOT_STORAGE_BACKEND")
    if not dependencies["snapshot_storage_bucket"]:
        missing.append("SNAPSHOT_STORAGE_BUCKET")
    if not dependencies["report_storage_bucket"]:
        missing.append("REPORT_STORAGE_BUCKET")
    if not dependencies["index_backend"]:
        missing.append("INDEX_BACKEND")
    if dependencies["index_backend"] == "opensearch":
        if not dependencies["opensearch_base_url"]:
            missing.append("OPENSEARCH_BASE_URL")
        if not dependencies["opensearch_index_name"]:
            missing.append("OPENSEARCH_INDEX_NAME")

    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "failed_stage": "CONFIGURATION",
                "reason": "missing_configuration",
                "missing": missing,
                "next_action": (
                    "Set the missing environment variables and restart the orchestrator."
                ),
            },
        )


def _running_mode(dependencies: dict[str, Any]) -> str:
    return "+".join(
        [
            str(dependencies["search_mode"]),
            str(dependencies["index_mode"]),
            str(dependencies["llm_mode"]),
        ]
    )


def _serialize_counts(counts: Any) -> PipelineCountsResponse:
    return PipelineCountsResponse(
        search_queries=counts.search_queries,
        candidate_urls=counts.candidate_urls,
        fetch_attempts=counts.fetch_attempts,
        content_snapshots=counts.content_snapshots,
        source_documents=counts.source_documents,
        source_chunks=counts.source_chunks,
        indexed_chunks=counts.indexed_chunks,
        claims=counts.claims,
        claim_evidence=counts.claim_evidence,
        report_artifacts=counts.report_artifacts,
    )
