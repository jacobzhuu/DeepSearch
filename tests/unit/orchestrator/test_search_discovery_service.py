from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.orm import Session

from packages.db.repositories import (
    CandidateUrlRepository,
    ResearchRunRepository,
    SearchQueryRepository,
)
from services.orchestrator.app.planning import PlannedSearchQuery
from services.orchestrator.app.search import (
    SearchProvider,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    SimpleQueryExpansionStrategy,
)
from services.orchestrator.app.services.research_tasks import create_research_task_service
from services.orchestrator.app.services.search_discovery import (
    SearchDiscoveryConflictError,
    SearchDiscoveryService,
    create_search_discovery_service,
)


@dataclass
class StaticSearchProvider:
    responses: dict[str, tuple[SearchResultItem, ...]]
    metadata: dict[str, Any] | None = None
    name: str = "searxng"

    def __post_init__(self) -> None:
        self.requests: list[SearchRequest] = []

    def search(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        results = self.responses.get(request.query_text, ())
        return SearchResponse(
            provider=self.name,
            source_engines=tuple(
                sorted(
                    {
                        result.source_engine
                        for result in results
                        if result.source_engine is not None and result.source_engine.strip()
                    }
                )
            ),
            result_count=len(results),
            results=results,
            metadata=self.metadata or {"request_query": request.query_text},
        )


def _create_search_service(
    db_session: Session,
    *,
    provider: SearchProvider,
    max_results_per_query: int = 5,
) -> SearchDiscoveryService:
    return create_search_discovery_service(
        db_session,
        search_provider=provider,
        query_expansion_strategy=SimpleQueryExpansionStrategy(max_domain_expansions=2),
        max_results_per_query=max_results_per_query,
    )


def test_discover_candidates_persists_search_queries_and_deduped_urls(db_session: Session) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="NVIDIA open model stack",
        constraints={
            "domains_allow": ["example.com", "allowed.com"],
            "domains_deny": ["blocked.example.com"],
            "max_urls": 5,
            "language": "zh-CN",
        },
    )
    provider = StaticSearchProvider(
        responses={
            "NVIDIA open model stack": (
                SearchResultItem(
                    url="https://example.com/a/?utm_source=x&b=2&a=1#frag",
                    title="Example A",
                    snippet="Snippet A",
                    source_engine="google",
                    rank=1,
                ),
                SearchResultItem(
                    url="https://blocked.example.com/secret",
                    title="Blocked",
                    snippet=None,
                    source_engine="google",
                    rank=2,
                ),
                SearchResultItem(
                    url="ftp://example.com/archive",
                    title="Archive",
                    snippet=None,
                    source_engine="google",
                    rank=3,
                ),
                SearchResultItem(
                    url="https://allowed.com/path?b=2&a=1",
                    title="Allowed",
                    snippet="Snippet B",
                    source_engine="bing",
                    rank=4,
                ),
            ),
            "site:example.com NVIDIA open model stack": (
                SearchResultItem(
                    url="https://example.com/a/?a=1&b=2",
                    title="Example duplicate",
                    snippet="Duplicate",
                    source_engine="google",
                    rank=1,
                ),
                SearchResultItem(
                    url="https://example.com/deep/path/",
                    title="Example C",
                    snippet="Snippet C",
                    source_engine="bing",
                    rank=2,
                ),
            ),
            "site:allowed.com NVIDIA open model stack": (),
        }
    )
    service = _create_search_service(db_session, provider=provider)

    result = service.discover_candidates(task.id)
    persisted_queries = SearchQueryRepository(db_session).list_for_task(task.id)
    persisted_candidates = CandidateUrlRepository(db_session).list_for_task(task.id)

    assert result.run.round_no == 1
    assert len(result.search_queries) == 3
    assert len(persisted_queries) == 3
    assert result.duplicates_skipped == 1
    assert result.filtered_out == 2
    assert [candidate.canonical_url for candidate in persisted_candidates] == [
        "https://example.com/a/?a=1&b=2",
        "https://allowed.com/path?a=1&b=2",
        "https://example.com/deep/path/",
    ]
    assert persisted_candidates[0].metadata_json["source_engine"] == "google"
    assert persisted_candidates[0].metadata_json["task_revision_no"] == 1
    first_query_payload = persisted_queries[0].raw_response_json
    assert first_query_payload is not None
    assert first_query_payload["task_revision_no"] == 1
    assert provider.requests[0].language == "zh-CN"


def test_discover_candidates_creates_new_run_after_revision(db_session: Session) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="Original research query",
        constraints={"domains_allow": ["example.com"]},
    )
    provider = StaticSearchProvider(
        responses={
            "Original research query": (
                SearchResultItem(
                    url="https://example.com/original",
                    title="Original",
                    snippet=None,
                    source_engine="google",
                    rank=1,
                ),
            ),
            "site:example.com Original research query": (),
            "Revised research query": (
                SearchResultItem(
                    url="https://example.com/revised",
                    title="Revised",
                    snippet=None,
                    source_engine="google",
                    rank=1,
                ),
            ),
            "site:example.com Revised research query": (),
        }
    )
    service = _create_search_service(db_session, provider=provider)

    first_result = service.discover_candidates(task.id)
    task_service.revise_task(
        task.id,
        query="Revised research query",
        constraints={"max_rounds": 2},
    )
    second_result = service.discover_candidates(task.id)

    runs = ResearchRunRepository(db_session).list_for_task(task.id)
    persisted_queries = SearchQueryRepository(db_session).list_for_task(task.id)

    assert first_result.run.round_no == 1
    assert second_result.run.round_no == 2
    assert [run.round_no for run in runs] == [1, 2]
    assert [item.round_no for item in persisted_queries] == [1, 1, 2, 2]
    query_payloads = [item.raw_response_json for item in persisted_queries]
    assert all(payload is not None for payload in query_payloads)
    assert [payload["task_revision_no"] for payload in query_payloads if payload is not None] == [
        1,
        1,
        2,
        2,
    ]


def test_discover_candidates_rejects_paused_task(db_session: Session) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="Paused search task",
        constraints={"domains_allow": ["example.com"]},
    )
    task_service.pause_task(task.id)

    provider = StaticSearchProvider(responses={})
    service = _create_search_service(db_session, provider=provider)

    with pytest.raises(SearchDiscoveryConflictError):
        service.discover_candidates(task.id)


def test_discover_candidates_uses_deduped_planner_queries(db_session: Session) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="What is SearXNG and how does it work?",
        constraints={"max_urls": 3},
    )
    provider = StaticSearchProvider(
        responses={
            "SearXNG official documentation what is SearXNG": (
                SearchResultItem(
                    url="https://docs.searxng.org/",
                    title="SearXNG docs",
                    snippet="Official docs",
                    source_engine="fake",
                    rank=1,
                ),
            ),
            "SearXNG privacy not storing user information": (
                SearchResultItem(
                    url="https://docs.searxng.org/user/about.html",
                    title="SearXNG privacy",
                    snippet="Privacy docs",
                    source_engine="fake",
                    rank=1,
                ),
            ),
            "What is SearXNG and how does it work?": (
                SearchResultItem(
                    url="https://en.wikipedia.org/wiki/SearXNG",
                    title="SearXNG",
                    snippet="Reference",
                    source_engine="fake",
                    rank=1,
                ),
            ),
        }
    )
    service = _create_search_service(db_session, provider=provider)

    result = service.discover_candidates(
        task.id,
        planned_search_queries=[
            PlannedSearchQuery(
                query_text="SearXNG official documentation what is SearXNG",
                rationale="official overview",
                expected_source_type="official_docs",
                priority=1,
                query_source="guardrail_query",
            ),
            PlannedSearchQuery(
                query_text="SearXNG official documentation what is SearXNG",
                rationale="duplicate",
                expected_source_type="official_docs",
                priority=2,
            ),
            PlannedSearchQuery(
                query_text="SearXNG privacy not storing user information",
                rationale="privacy",
                expected_source_type="official_docs",
                priority=3,
            ),
        ],
    )
    persisted_queries = SearchQueryRepository(db_session).list_for_task(task.id)

    assert [request.query_text for request in provider.requests] == [
        "SearXNG official documentation what is SearXNG",
        "SearXNG privacy not storing user information",
        "What is SearXNG and how does it work?",
    ]
    assert [item.search_query.query_text for item in result.search_queries] == [
        "SearXNG official documentation what is SearXNG",
        "SearXNG privacy not storing user information",
        "What is SearXNG and how does it work?",
    ]
    canonical_urls = [candidate.canonical_url for candidate in result.candidate_urls]
    assert "https://docs.searxng.org/user/about.html" in canonical_urls
    assert "https://en.wikipedia.org/wiki/SearXNG" in canonical_urls
    about_candidate = next(
        candidate
        for candidate in result.candidate_urls
        if candidate.canonical_url == "https://docs.searxng.org/user/about.html"
    )
    assert about_candidate.metadata_json["known_path_candidate"] is True
    assert about_candidate.metadata_json["source_engine"] == "deterministic_known_path"
    assert persisted_queries[0].raw_response_json["expansion_kind"] == "research_plan"
    assert (
        persisted_queries[0].raw_response_json["expansion_metadata"]["expected_source_type"]
        == "official_docs"
    )
    assert persisted_queries[0].raw_response_json["expansion_metadata"]["query_source"] == (
        "guardrail_query"
    )
