from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from packages.db.models import SearchQuery
from services.orchestrator.app.api.schemas.search_discovery import (
    CandidateUrlListResponse,
    CandidateUrlResponse,
    SearchDiscoveryResponse,
    SearchQueryListResponse,
    SearchQueryRecordResponse,
    SearchQuerySummaryResponse,
)
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.search import (
    QueryExpansionStrategy,
    SearchProvider,
    SearchProviderError,
    SearXNGSearchProvider,
    SimpleQueryExpansionStrategy,
    SmokeSearchProvider,
)
from services.orchestrator.app.services.research_tasks import TaskNotFoundError
from services.orchestrator.app.services.search_discovery import (
    SearchDiscoveryConflictError,
    SearchDiscoveryService,
    create_search_discovery_service,
)
from services.orchestrator.app.settings import get_settings

router = APIRouter(prefix="/api/v1/research/tasks", tags=["search-discovery"])
SessionDep = Annotated[Session, Depends(get_db_session)]


def get_search_provider() -> SearchProvider:
    settings = get_settings()
    normalized_provider = settings.search_provider.strip().lower()
    if normalized_provider == "smoke":
        return SmokeSearchProvider()
    if normalized_provider != "searxng":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "failed_stage": "CONFIGURATION",
                "reason": "unsupported_search_provider",
                "message": f"unsupported search provider: {settings.search_provider}",
                "next_action": (
                    "Set SEARCH_PROVIDER to searxng or smoke and restart the orchestrator."
                ),
            },
        )
    return SearXNGSearchProvider(
        base_url=settings.searxng_base_url,
        timeout_seconds=settings.searxng_timeout_seconds,
    )


def get_query_expansion_strategy() -> QueryExpansionStrategy:
    settings = get_settings()
    return SimpleQueryExpansionStrategy(
        max_domain_expansions=settings.query_expansion_max_domains,
    )


def get_search_discovery_service(
    session: SessionDep,
    search_provider: Annotated[SearchProvider, Depends(get_search_provider)],
    query_expansion_strategy: Annotated[
        QueryExpansionStrategy,
        Depends(get_query_expansion_strategy),
    ],
) -> SearchDiscoveryService:
    settings = get_settings()
    return create_search_discovery_service(
        session,
        search_provider=search_provider,
        query_expansion_strategy=query_expansion_strategy,
        max_results_per_query=settings.search_max_results_per_query,
    )


ServiceDep = Annotated[
    SearchDiscoveryService,
    Depends(get_search_discovery_service),
]


@router.post(
    "/{task_id}/searches",
    response_model=SearchDiscoveryResponse,
    status_code=status.HTTP_201_CREATED,
)
def discover_task_searches(task_id: UUID, service: ServiceDep) -> SearchDiscoveryResponse:
    try:
        result = service.discover_candidates(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    except SearchDiscoveryConflictError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    except SearchProviderError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "failed_stage": "SEARCHING",
                **error.to_payload(),
                "next_action": (
                    "Verify SEARCH_PROVIDER and SEARXNG_BASE_URL. The endpoint must return "
                    "SearXNG JSON from /search?format=json."
                ),
            },
        ) from error

    return SearchDiscoveryResponse(
        task_id=result.task.id,
        run_id=result.run.id,
        round_no=result.run.round_no,
        revision_no=result.task.revision_no,
        search_queries=[
            SearchQuerySummaryResponse(
                search_query_id=item.search_query.id,
                query_text=item.search_query.query_text,
                provider=item.search_query.provider,
                source_engines=_search_query_source_engines(item.search_query),
                round_no=item.search_query.round_no,
                issued_at=item.search_query.issued_at,
                candidates_added=item.candidates_added,
                duplicates_skipped=item.duplicates_skipped,
                filtered_out=item.filtered_out,
            )
            for item in result.search_queries
        ],
        candidate_urls_added=len(result.candidate_urls),
        duplicates_skipped=result.duplicates_skipped,
        filtered_out=result.filtered_out,
    )


@router.get("/{task_id}/search-queries", response_model=SearchQueryListResponse)
def list_task_search_queries(task_id: UUID, service: ServiceDep) -> SearchQueryListResponse:
    try:
        search_queries = service.list_search_queries(task_id)
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return SearchQueryListResponse(
        task_id=task_id,
        search_queries=[
            SearchQueryRecordResponse(
                search_query_id=search_query.id,
                query_text=search_query.query_text,
                provider=search_query.provider,
                source_engines=_search_query_source_engines(search_query),
                round_no=search_query.round_no,
                issued_at=search_query.issued_at,
                result_count=_search_query_result_count(search_query),
                metadata=search_query.raw_response_json or {},
            )
            for search_query in search_queries
        ],
    )


@router.get("/{task_id}/candidate-urls", response_model=CandidateUrlListResponse)
def list_task_candidate_urls(
    task_id: UUID,
    service: ServiceDep,
    domain: Annotated[str | None, Query()] = None,
    selected: Annotated[bool | None, Query()] = None,
    limit: Annotated[int | None, Query(ge=1, le=500)] = None,
) -> CandidateUrlListResponse:
    try:
        candidate_urls = service.list_candidate_urls(
            task_id,
            domain=domain.strip().lower() if domain is not None else None,
            selected=selected,
            limit=limit,
        )
    except TaskNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    return CandidateUrlListResponse(
        task_id=task_id,
        candidate_urls=[
            CandidateUrlResponse(
                candidate_url_id=candidate.id,
                search_query_id=candidate.search_query_id,
                original_url=candidate.original_url,
                canonical_url=candidate.canonical_url,
                domain=candidate.domain,
                title=candidate.title,
                rank=candidate.rank,
                selected=candidate.selected,
                metadata=candidate.metadata_json,
            )
            for candidate in candidate_urls
        ],
    )


def _search_query_source_engines(search_query: SearchQuery) -> list[str]:
    raw_payload = search_query.raw_response_json or {}
    source_engines = raw_payload.get("source_engines", [])
    if not isinstance(source_engines, list):
        return []
    return [item for item in source_engines if isinstance(item, str)]


def _search_query_result_count(search_query: SearchQuery) -> int:
    raw_payload = search_query.raw_response_json or {}
    result_count = raw_payload.get("result_count", 0)
    if not isinstance(result_count, int):
        return 0
    return result_count
