from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from services.orchestrator.app.api.schemas.pipeline import (
    PipelineCountsResponse,
    PipelineRunResponse,
)
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.services.debug_pipeline import collect_debug_pipeline_counts
from services.orchestrator.app.services.research_tasks import (
    TaskNotFoundError,
    TaskStateConflictError,
    create_research_task_service,
)
from services.orchestrator.app.settings import get_settings

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
) -> PipelineRunResponse:
    settings = get_settings()
    dependencies = _dependency_summary(settings)
    _validate_required_configuration(dependencies)

    try:
        task = create_research_task_service(session).enqueue_task(
            task_id,
            dependencies=dependencies,
            running_mode=_running_mode(dependencies),
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except TaskStateConflictError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "failed_stage": "PRECONDITION",
                "reason": "pipeline_precondition_failed",
                "message": str(error),
                "next_action": (
                    "Create or revise a task so it is in PLANNED before queueing a run."
                ),
            },
        ) from error
    counts = collect_debug_pipeline_counts(session, task_id)

    return PipelineRunResponse(
        task_id=task.id,
        status=task.status,
        completed=False,
        running_mode=_running_mode(dependencies),
        stages_completed=[],
        counts=_serialize_counts(counts),
        report_artifact_id=None,
        report_version=None,
        report_markdown_preview=None,
        failure=None,
        dependencies=dependencies,
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
        "uses_llm_api": _uses_llm_api(settings),
        "llm_mode": _llm_mode(settings),
        "llm_provider": settings.llm_provider.strip().lower() or "noop",
        "llm_model": settings.llm_model.strip(),
        "llm_base_url_configured": bool(settings.llm_base_url.strip()),
        "research_planner_enabled": bool(
            settings.research_planner_enabled and settings.llm_enabled
        ),
        "llm_report_writer_enabled": _llm_report_writer_configured(settings),
        "report_writer_mode": (
            "llm-grounded" if _llm_report_writer_configured(settings) else "deterministic"
        ),
        "uses_worker_or_queue": True,
    }


def _llm_mode(settings: Any) -> str:
    planner_configured = bool(settings.research_planner_enabled and settings.llm_enabled)
    report_configured = _llm_report_writer_configured(settings)
    if report_configured and planner_configured:
        return "planner+report-LLM"
    if report_configured:
        return "report-LLM"
    if not planner_configured:
        return "no-LLM"
    normalized_provider = settings.llm_provider.strip().lower() or "noop"
    if normalized_provider == "noop":
        return "planner-noop"
    return "planner-LLM"


def _uses_llm_api(settings: Any) -> bool:
    return bool(
        settings.llm_enabled
        and settings.llm_provider.strip().lower() not in {"", "noop"}
        and (settings.research_planner_enabled or settings.llm_report_writer_enabled)
    )


def _llm_report_writer_configured(settings: Any) -> bool:
    return bool(
        settings.llm_enabled
        and settings.llm_report_writer_enabled
        and settings.llm_provider.strip().lower() not in {"", "noop"}
    )


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
