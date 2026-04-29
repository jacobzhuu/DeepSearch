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
from services.orchestrator.app.api.schemas.debug_pipeline import (
    DebugPipelineCountsResponse,
    DebugPipelineFailureResponse,
    DebugPipelineResponse,
)
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.indexing import ChunkIndexBackend
from services.orchestrator.app.planning import create_research_planner_service
from services.orchestrator.app.search import QueryExpansionStrategy, SearchProvider
from services.orchestrator.app.services.acquisition import create_acquisition_service
from services.orchestrator.app.services.claims import create_claim_drafting_service
from services.orchestrator.app.services.debug_pipeline import (
    ACQUISITION_ALLOWED_STATUSES,
    DEBUG_EVENT_PREFIX,
    DEBUG_EVENT_SOURCE,
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

router = APIRouter(prefix="/api/v1/research/tasks", tags=["debug-pipeline"])
SessionDep = Annotated[Session, Depends(get_db_session)]


@router.post(
    "/{task_id}/debug/run-real-pipeline",
    response_model=DebugPipelineResponse,
    status_code=status.HTTP_200_OK,
)
def run_debug_real_pipeline(
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
) -> DebugPipelineResponse:
    settings = get_settings()
    if settings.app_env.strip().lower() != "development":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="debug real pipeline endpoint is only available when APP_ENV=development",
        )

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
        planner_service=create_research_planner_service(settings),
        dependencies=dependencies,
        fetch_limit=settings.acquisition_max_candidates_per_request,
        parse_limit=3,
        index_limit=10,
        claim_limit=5,
        event_source=DEBUG_EVENT_SOURCE,
        event_prefix=DEBUG_EVENT_PREFIX,
        target_successful_snapshots=settings.acquisition_target_successful_snapshots,
        min_answer_sources=settings.acquisition_min_answer_sources,
        max_supplemental_sources=settings.acquisition_max_supplemental_sources,
    )

    try:
        result = runner.run(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except DebugPipelinePreconditionError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error

    return DebugPipelineResponse(
        task_id=result.task.id,
        status=result.task.status,
        completed=result.completed,
        stages_completed=result.stages_completed,
        counts=_serialize_counts(result.counts),
        report_artifact_id=result.report_artifact_id,
        report_version=result.report_version,
        report_markdown_preview=result.report_markdown_preview,
        failure=(
            DebugPipelineFailureResponse(
                stage=result.failure.stage,
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
    return {
        "search_provider": settings.search_provider,
        "search_mode": settings.search_provider,
        "searxng_base_url": settings.searxng_base_url,
        "snapshot_storage_backend": settings.snapshot_storage_backend,
        "snapshot_storage_root": settings.snapshot_storage_root,
        "snapshot_storage_bucket": settings.snapshot_storage_bucket,
        "report_storage_bucket": settings.report_storage_bucket,
        "index_backend": settings.index_backend,
        "index_mode": settings.index_backend,
        "opensearch_base_url": settings.opensearch_base_url,
        "opensearch_index_name": settings.opensearch_index_name,
        "uses_llm_api": bool(
            settings.llm_enabled
            and settings.research_planner_enabled
            and settings.llm_provider.strip().lower() not in {"", "noop"}
        ),
        "llm_mode": _llm_mode(settings),
        "llm_provider": settings.llm_provider.strip().lower() or "noop",
        "llm_model": settings.llm_model.strip(),
        "llm_base_url_configured": bool(settings.llm_base_url.strip()),
        "research_planner_enabled": bool(
            settings.research_planner_enabled and settings.llm_enabled
        ),
        "uses_worker_or_queue": False,
    }


def _llm_mode(settings: Any) -> str:
    if not settings.research_planner_enabled or not settings.llm_enabled:
        return "no-LLM"
    normalized_provider = settings.llm_provider.strip().lower() or "noop"
    if normalized_provider == "noop":
        return "planner-noop"
    return "planner-LLM"


def _validate_required_configuration(dependencies: dict[str, Any]) -> None:
    missing = [
        name
        for name in (
            "searxng_base_url",
            "snapshot_storage_backend",
            "snapshot_storage_bucket",
            "report_storage_bucket",
            "index_backend",
            "opensearch_base_url",
            "opensearch_index_name",
        )
        if not str(dependencies.get(name) or "").strip()
    ]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "stage": "CONFIGURATION",
                "reason": "missing_configuration",
                "missing": missing,
            },
        )


def _serialize_counts(counts: Any) -> DebugPipelineCountsResponse:
    return DebugPipelineCountsResponse(
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
