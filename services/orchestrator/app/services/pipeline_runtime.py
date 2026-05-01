from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from services.orchestrator.app.acquisition import HttpAcquisitionClient, SmokeAcquisitionClient
from services.orchestrator.app.indexing import ChunkIndexBackend, build_chunk_index_backend
from services.orchestrator.app.llm import create_llm_provider
from services.orchestrator.app.planning import create_research_planner_service
from services.orchestrator.app.search import (
    QueryExpansionStrategy,
    SearchProvider,
    SearXNGSearchProvider,
    SimpleQueryExpansionStrategy,
    SmokeSearchProvider,
)
from services.orchestrator.app.services.acquisition import create_acquisition_service
from services.orchestrator.app.services.claims import create_claim_drafting_service
from services.orchestrator.app.services.debug_pipeline import (
    ACQUISITION_ALLOWED_STATUSES,
    DRAFT_ALLOWED_STATUSES,
    INDEXING_ALLOWED_STATUSES,
    PARSING_ALLOWED_STATUSES,
    PIPELINE_EVENT_PREFIX,
    PIPELINE_EVENT_SOURCE,
    SEARCH_ALLOWED_STATUSES,
    VERIFY_ALLOWED_STATUSES,
    DebugRealPipelineRunner,
)
from services.orchestrator.app.services.indexing import create_indexing_service
from services.orchestrator.app.services.parsing import create_parsing_service
from services.orchestrator.app.services.reporting import create_report_synthesis_service
from services.orchestrator.app.settings import Settings
from services.orchestrator.app.storage import SnapshotObjectStore, build_snapshot_object_store


class PipelineConfigurationError(RuntimeError):
    def __init__(self, *, missing: list[str]) -> None:
        super().__init__("pipeline configuration is incomplete")
        self.missing = missing

    def to_payload(self) -> dict[str, Any]:
        return {
            "failed_stage": "CONFIGURATION",
            "reason": "missing_configuration",
            "missing": list(self.missing),
            "next_action": "Set the missing environment variables and restart the worker.",
        }


def create_pipeline_runner(
    session: Session,
    *,
    settings: Settings,
    search_provider: SearchProvider | None = None,
    query_expansion_strategy: QueryExpansionStrategy | None = None,
    http_client: HttpAcquisitionClient | None = None,
    snapshot_object_store: SnapshotObjectStore | None = None,
    index_backend: ChunkIndexBackend | None = None,
    claim_index_backend: ChunkIndexBackend | None = None,
    event_source: str = PIPELINE_EVENT_SOURCE,
    event_prefix: str = PIPELINE_EVENT_PREFIX,
) -> DebugRealPipelineRunner:
    dependencies = pipeline_dependency_summary(settings)
    validate_pipeline_configuration(dependencies)
    object_store = snapshot_object_store or build_runtime_object_store(settings)
    resolved_index_backend = index_backend or build_runtime_index_backend(settings)
    resolved_claim_index_backend = claim_index_backend or resolved_index_backend
    llm_report_provider = (
        create_llm_provider(settings) if llm_report_writer_configured(settings) else None
    )
    llm_source_judge_provider = (
        create_llm_provider(settings) if llm_source_judge_configured(settings) else None
    )
    return DebugRealPipelineRunner(
        session,
        search_service=create_acquisition_search_service(
            session,
            settings=settings,
            search_provider=search_provider,
            query_expansion_strategy=query_expansion_strategy,
        ),
        acquisition_service=create_acquisition_service(
            session,
            http_client=http_client or build_runtime_http_client(settings),
            snapshot_object_store=object_store,
            snapshot_bucket=settings.snapshot_storage_bucket,
            max_candidates_per_request=settings.acquisition_max_candidates_per_request,
            allowed_statuses=ACQUISITION_ALLOWED_STATUSES,
        ),
        parsing_service=create_parsing_service(
            session,
            snapshot_object_store=object_store,
            allowed_statuses=PARSING_ALLOWED_STATUSES,
        ),
        indexing_service=create_indexing_service(
            session,
            index_backend=resolved_index_backend,
            indexing_max_chunks_per_request=min(settings.indexing_max_chunks_per_request, 10),
            retrieval_max_results_per_request=settings.retrieval_max_results_per_request,
            allowed_statuses=INDEXING_ALLOWED_STATUSES,
        ),
        claims_service=create_claim_drafting_service(
            session,
            index_backend=resolved_claim_index_backend,
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
            object_store=object_store,
            report_storage_bucket=settings.report_storage_bucket,
            llm_provider=llm_report_provider,
            llm_model=settings.llm_model,
            llm_report_writer_enabled=llm_report_provider is not None,
            llm_report_max_output_tokens=settings.llm_report_max_output_tokens,
        ),
        planner_service=create_research_planner_service(settings),
        source_judge_service=create_source_judge_service(
            settings,
            provider=llm_source_judge_provider,
        ),
        dependencies=dependencies,
        fetch_limit=settings.acquisition_max_candidates_per_request,
        parse_limit=3,
        index_limit=10,
        claim_limit=5,
        event_source=event_source,
        event_prefix=event_prefix,
        target_successful_snapshots=settings.acquisition_target_successful_snapshots,
        min_answer_sources=settings.acquisition_min_answer_sources,
        max_supplemental_sources=settings.acquisition_max_supplemental_sources,
        max_gap_rounds=settings.research_gap_max_rounds,
        gap_max_queries_per_round=settings.research_gap_max_queries_per_round,
    )


def create_acquisition_search_service(
    session: Session,
    *,
    settings: Settings,
    search_provider: SearchProvider | None = None,
    query_expansion_strategy: QueryExpansionStrategy | None = None,
) -> Any:
    from services.orchestrator.app.services.search_discovery import (
        create_search_discovery_service,
    )

    return create_search_discovery_service(
        session,
        search_provider=search_provider or build_runtime_search_provider(settings),
        query_expansion_strategy=query_expansion_strategy
        or SimpleQueryExpansionStrategy(
            max_domain_expansions=settings.query_expansion_max_domains,
        ),
        max_results_per_query=min(settings.search_max_results_per_query, 5),
        allowed_statuses=SEARCH_ALLOWED_STATUSES,
    )


def pipeline_dependency_summary(settings: Settings) -> dict[str, Any]:
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
        "uses_llm_api": uses_llm_api(settings),
        "llm_mode": llm_mode(settings),
        "llm_provider": settings.llm_provider.strip().lower() or "noop",
        "llm_model": settings.llm_model.strip(),
        "llm_base_url_configured": bool(settings.llm_base_url.strip()),
        "research_planner_enabled": bool(
            settings.research_planner_enabled and settings.llm_enabled
        ),
        "llm_report_writer_enabled": llm_report_writer_configured(settings),
        "llm_source_judge_enabled": llm_source_judge_configured(settings),
        "llm_source_judge_active_rerank": bool(
            settings.llm_source_judge_active_rerank and llm_source_judge_configured(settings)
        ),
        "report_writer_mode": (
            "llm-grounded" if llm_report_writer_configured(settings) else "deterministic"
        ),
        "uses_worker_or_queue": True,
    }


def validate_pipeline_configuration(dependencies: dict[str, Any]) -> None:
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
        raise PipelineConfigurationError(missing=missing)


def build_runtime_search_provider(settings: Settings) -> SearchProvider:
    normalized_provider = settings.search_provider.strip().lower()
    if normalized_provider == "smoke":
        return SmokeSearchProvider()
    return SearXNGSearchProvider(
        base_url=settings.searxng_base_url,
        timeout_seconds=settings.searxng_timeout_seconds,
    )


def build_runtime_http_client(settings: Settings) -> HttpAcquisitionClient:
    if settings.search_provider.strip().lower() == "smoke":
        return SmokeAcquisitionClient()
    return HttpAcquisitionClient(
        timeout_seconds=settings.acquisition_timeout_seconds,
        max_redirects=settings.acquisition_max_redirects,
        max_response_bytes=settings.acquisition_max_response_bytes,
        user_agent=settings.acquisition_user_agent,
    )


def build_runtime_object_store(settings: Settings) -> SnapshotObjectStore:
    return build_snapshot_object_store(
        backend=settings.snapshot_storage_backend,
        root_directory=settings.snapshot_storage_root,
        minio_endpoint=settings.minio_endpoint,
        minio_access_key=settings.minio_access_key,
        minio_secret_key=settings.minio_secret_key,
        minio_secure=settings.minio_secure,
        minio_region=settings.minio_region,
        required_buckets=[settings.snapshot_storage_bucket, settings.report_storage_bucket],
    )


def build_runtime_index_backend(settings: Settings) -> ChunkIndexBackend:
    return build_chunk_index_backend(
        backend=settings.index_backend,
        opensearch_base_url=settings.opensearch_base_url,
        opensearch_index_name=settings.opensearch_index_name,
        opensearch_username=settings.opensearch_username,
        opensearch_password=settings.opensearch_password,
        opensearch_verify_tls=settings.opensearch_verify_tls,
        opensearch_ca_bundle_path=settings.opensearch_ca_bundle_path,
        opensearch_timeout_seconds=settings.opensearch_timeout_seconds,
        opensearch_validate_connectivity=False,
    )


def llm_mode(settings: Settings) -> str:
    planner_configured = bool(settings.research_planner_enabled and settings.llm_enabled)
    report_configured = llm_report_writer_configured(settings)
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


def uses_llm_api(settings: Settings) -> bool:
    return bool(
        settings.llm_enabled
        and settings.llm_provider.strip().lower() not in {"", "noop"}
        and (
            settings.research_planner_enabled
            or settings.llm_report_writer_enabled
            or settings.llm_source_judge_enabled
        )
    )


def llm_report_writer_configured(settings: Settings) -> bool:
    return bool(
        settings.llm_enabled
        and settings.llm_report_writer_enabled
        and settings.llm_provider.strip().lower() not in {"", "noop"}
    )


def llm_source_judge_configured(settings: Settings) -> bool:
    return bool(settings.llm_enabled and settings.llm_source_judge_enabled)


def create_source_judge_service(settings: Settings, *, provider: Any) -> Any:
    from services.orchestrator.app.research_quality import SourceJudgeService

    return SourceJudgeService(
        enabled=llm_source_judge_configured(settings),
        active_rerank=False,
        provider=provider,
        model=settings.llm_model,
        max_candidates=settings.llm_source_judge_max_candidates,
    )


def pipeline_running_mode(dependencies: dict[str, Any]) -> str:
    return "+".join(
        [
            str(dependencies["search_mode"]),
            str(dependencies["index_mode"]),
            str(dependencies["llm_mode"]),
        ]
    )
