from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.orm import Session

from packages.db.models import CandidateUrl, ResearchRun, SearchQuery
from packages.db.repositories import (
    CandidateUrlRepository,
    ResearchRunRepository,
    SearchQueryRepository,
)
from services.orchestrator.app.planning import PlannedSearchQuery
from services.orchestrator.app.search import (
    SearchProvider,
    SearchProviderError,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    SimpleQueryExpansionStrategy,
)
from services.orchestrator.app.services.research_tasks import create_research_task_service
from services.orchestrator.app.services.search_discovery import (
    SearchDiscoveryConflictError,
    SearchDiscoveryService,
    _add_known_path_candidates,
    _merge_official_github_raw_readme_derivatives,
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


def _raw_response(search_query: SearchQuery) -> dict[str, Any]:
    assert search_query.raw_response_json is not None
    return search_query.raw_response_json


@dataclass
class FailingSearchProvider:
    reason: str = "searxng_empty_results_with_unresponsive_engines"
    name: str = "searxng"

    def __post_init__(self) -> None:
        self.requests: list[SearchRequest] = []

    def search(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        raise SearchProviderError(
            reason=self.reason,
            message=(
                "SearXNG returned no results and reported unresponsive engines: brave, duckduckgo."
            ),
            status_code=200,
            content_type="application/json",
            body_preview=None,
            unresponsive_engines=["brave", "duckduckgo"],
        )


@dataclass
class SequencedSearchProvider:
    responses: dict[str, tuple[SearchResultItem, ...]]
    failures: dict[str, SearchProviderError]
    name: str = "searxng"

    def __post_init__(self) -> None:
        self.requests: list[SearchRequest] = []

    def search(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        if request.query_text in self.failures:
            raise self.failures[request.query_text]
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
            metadata={"request_query": request.query_text},
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
        "https://example.com/a?a=1&b=2",
        "https://allowed.com/path?a=1&b=2",
        "https://example.com/deep/path",
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
        constraints={"max_urls": 10},
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
    assert result.search_queries[0].search_query.provider == "authoritative-source-resolver"
    assert [item.search_query.query_text for item in result.search_queries[1:]] == [
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
    resolver_raw_response = _raw_response(persisted_queries[0])
    assert resolver_raw_response["known_source_resolver"]["selected_count"] >= 3
    first_raw_response = _raw_response(persisted_queries[1])
    assert first_raw_response["expansion_kind"] == "research_plan"
    assert first_raw_response["expansion_metadata"]["expected_source_type"] == "official_docs"
    assert first_raw_response["expansion_metadata"]["query_source"] == ("guardrail_query")


def test_discover_candidates_applies_max_urls_across_authoritative_and_provider_results(
    db_session: Session,
) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="What is LangGraph and how does it work?",
        constraints={"max_urls": 8},
    )
    provider = StaticSearchProvider(
        responses={
            "What is LangGraph and how does it work?": (
                SearchResultItem(
                    url="https://example.com/provider-one",
                    title="Provider one",
                    snippet="Provider result one",
                    source_engine="fake",
                    rank=1,
                ),
                SearchResultItem(
                    url="https://example.com/provider-two",
                    title="Provider two",
                    snippet="Provider result two",
                    source_engine="fake",
                    rank=2,
                ),
            )
        }
    )
    service = _create_search_service(db_session, provider=provider, max_results_per_query=5)

    service.discover_candidates(task.id, include_default_expansions=False)
    persisted_candidates = CandidateUrlRepository(db_session).list_for_task(task.id)
    authoritative_candidates = [
        candidate
        for candidate in persisted_candidates
        if (candidate.metadata_json or {}).get("candidate_source")
        == "authoritative_source_resolver"
    ]
    provider_candidates = [
        candidate
        for candidate in persisted_candidates
        if (candidate.metadata_json or {}).get("provider") == "searxng"
    ]

    assert len(persisted_candidates) == 8
    assert len(authoritative_candidates) == 8
    assert len(provider_candidates) == 0
    assert provider.requests == []


def test_discover_candidates_does_not_call_provider_when_authoritative_candidates_fill_budget(
    db_session: Session,
) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="What is LangGraph and how does it work?",
        constraints={"max_urls": 3},
    )
    provider = StaticSearchProvider(
        responses={
            "What is LangGraph and how does it work?": (
                SearchResultItem(
                    url="https://example.com/provider-one",
                    title="Provider one",
                    snippet="Provider result one",
                    source_engine="fake",
                    rank=1,
                ),
            )
        }
    )
    service = _create_search_service(db_session, provider=provider, max_results_per_query=5)

    service.discover_candidates(task.id, include_default_expansions=False)
    persisted_candidates = CandidateUrlRepository(db_session).list_for_task(task.id)

    assert len(persisted_candidates) == 3
    assert all(
        (candidate.metadata_json or {}).get("candidate_source") == "authoritative_source_resolver"
        for candidate in persisted_candidates
    )
    assert provider.requests == []


def test_discover_candidates_injects_langgraph_known_path_fallback_on_unresponsive_search(
    db_session: Session,
) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="What is LangGraph and how does it work?",
        constraints={},
    )
    provider = FailingSearchProvider()
    service = _create_search_service(db_session, provider=provider)

    result = service.discover_candidates(
        task.id,
        planned_search_queries=[
            PlannedSearchQuery(
                query_text="LangGraph site:docs.langchain.com how it works",
                rationale="owned docs",
                expected_source_type="official_docs",
                priority=1,
            )
        ],
    )
    persisted_queries = SearchQueryRepository(db_session).list_for_task(task.id)
    persisted_candidates = CandidateUrlRepository(db_session).list_for_task(task.id)
    canonical_urls = [candidate.canonical_url for candidate in persisted_candidates]

    assert len(provider.requests) == 1
    assert result.search_queries[0].search_query.provider == "authoritative-source-resolver"
    assert result.search_queries[0].candidates_added >= 6
    assert "https://docs.langchain.com/oss/python/langgraph/overview" in canonical_urls
    assert "https://reference.langchain.com/python/langgraph/graph/state" in canonical_urls
    assert "https://github.com/langchain-ai/langgraph" in canonical_urls
    assert "https://pypi.org/project/langgraph" in canonical_urls
    assert all(
        candidate.metadata_json["candidate_source"]
        in {
            "authoritative_source_resolver",
            "known_path_fallback",
        }
        for candidate in persisted_candidates
    )
    fallback_candidates = [
        candidate
        for candidate in persisted_candidates
        if candidate.metadata_json["candidate_source"] == "known_path_fallback"
    ]
    assert fallback_candidates
    assert all(
        candidate.metadata_json["fallback_reason"]
        == "searxng_empty_results_with_unresponsive_engines"
        for candidate in fallback_candidates
    )
    assert all(
        candidate.metadata_json["original_search_provider"] == "searxng"
        for candidate in fallback_candidates
    )
    repo_candidate = next(
        candidate
        for candidate in persisted_candidates
        if candidate.canonical_url == "https://github.com/langchain-ai/langgraph"
    )
    assert repo_candidate.metadata_json["source_role"] == "official_repository"
    target_slots = repo_candidate.metadata_json.get("target_slots")
    assert isinstance(target_slots, list)
    assert "examples_use_cases" in target_slots
    assert "official_sources" in target_slots

    fallback_payload = _raw_response(persisted_queries[1])["known_path_fallback"]
    assert fallback_payload["known_path_fallback_applied"] is True
    assert fallback_payload["known_path_fallback_candidate_count"] >= 1
    assert fallback_payload["known_path_fallback_duplicates_skipped"] >= 5
    assert fallback_payload["query_count_attempted"] == 1
    assert fallback_payload["empty_query_count"] == 1
    assert fallback_payload["provider_error_type"] == "SearchProviderError"
    assert fallback_payload["failed_queries"][0]["query_text"] == (
        "LangGraph site:docs.langchain.com how it works"
    )


def test_discover_candidates_tolerates_later_langgraph_failure_when_candidates_exist(
    db_session: Session,
) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="What is LangGraph and how does it work?",
        constraints={},
    )
    provider = SequencedSearchProvider(
        responses={
            "LangGraph official docs overview": (
                SearchResultItem(
                    url="https://docs.langchain.com/oss/python/langgraph/overview",
                    title="LangGraph overview - Docs by LangChain",
                    snippet="Official docs",
                    source_engine="fake",
                    rank=1,
                ),
            )
        },
        failures={
            "LangGraph state graph reference": SearchProviderError(
                reason="searxng_empty_results_with_unresponsive_engines",
                message=(
                    "SearXNG returned no results and reported unresponsive engines: "
                    "brave, duckduckgo."
                ),
                status_code=200,
                content_type="application/json",
                body_preview=None,
                unresponsive_engines=["brave", "duckduckgo"],
            )
        },
    )
    service = _create_search_service(db_session, provider=provider)

    result = service.discover_candidates(
        task.id,
        planned_search_queries=[
            PlannedSearchQuery(
                query_text="LangGraph official docs overview",
                rationale="owned docs",
                expected_source_type="official_docs",
                priority=1,
            ),
            PlannedSearchQuery(
                query_text="LangGraph state graph reference",
                rationale="owned reference",
                expected_source_type="reference",
                priority=2,
            ),
            PlannedSearchQuery(
                query_text="LangGraph GitHub repository",
                rationale="upstream repository",
                expected_source_type="official_repository",
                priority=3,
            ),
        ],
    )
    persisted_queries = SearchQueryRepository(db_session).list_for_task(task.id)
    persisted_candidates = CandidateUrlRepository(db_session).list_for_task(task.id)

    assert [request.query_text for request in provider.requests] == [
        "LangGraph official docs overview",
        "LangGraph state graph reference",
    ]
    assert len(result.search_queries) == 3
    assert len(result.candidate_urls) >= 7
    assert len(persisted_candidates) >= 7
    fallback_payload = _raw_response(persisted_queries[2])["known_path_fallback"]
    assert fallback_payload["known_path_fallback_applied"] is True
    assert fallback_payload["known_path_fallback_candidate_count"] == 0
    assert fallback_payload["known_path_fallback_duplicates_skipped"] >= 6
    assert fallback_payload["available_candidate_count"] >= 7
    assert fallback_payload["search_provider_failure_tolerated"] is True


def test_discover_candidates_continues_after_first_provider_failure_when_later_query_succeeds(
    db_session: Session,
) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="What is ExampleFlow and how does routing work?",
        constraints={},
    )
    provider = SequencedSearchProvider(
        responses={
            "ExampleFlow routing official docs": (
                SearchResultItem(
                    url="https://example.org/exampleflow/routing",
                    title="ExampleFlow routing",
                    snippet="Routing overview.",
                    source_engine="fake",
                    rank=1,
                ),
            )
        },
        failures={
            "ExampleFlow general overview": SearchProviderError(
                reason="searxng_empty_results_with_unresponsive_engines",
                message=(
                    "SearXNG returned no results and reported unresponsive engines: "
                    "brave, duckduckgo."
                ),
                status_code=200,
                content_type="application/json",
                body_preview=None,
                unresponsive_engines=["brave", "duckduckgo"],
            )
        },
    )
    service = _create_search_service(db_session, provider=provider)

    result = service.discover_candidates(
        task.id,
        planned_search_queries=[
            PlannedSearchQuery(
                query_text="ExampleFlow general overview",
                rationale="overview",
                expected_source_type="general_web",
                priority=1,
            ),
            PlannedSearchQuery(
                query_text="ExampleFlow routing official docs",
                rationale="owned docs",
                expected_source_type="official_docs",
                priority=2,
            ),
        ],
        include_default_expansions=False,
    )
    persisted_queries = SearchQueryRepository(db_session).list_for_task(task.id)
    persisted_candidates = CandidateUrlRepository(db_session).list_for_task(task.id)

    assert [request.query_text for request in provider.requests] == [
        "ExampleFlow general overview",
        "ExampleFlow routing official docs",
    ]
    assert len(result.search_queries) == 2
    assert len(result.candidate_urls) == 1
    assert len(persisted_candidates) == 1
    failure_payload = _raw_response(persisted_queries[0])["search_provider_failure"]
    assert failure_payload["known_path_fallback_applied"] is False
    assert failure_payload["search_provider_failure_tolerated"] is True
    assert failure_payload["failed_queries"][0]["query_text"] == "ExampleFlow general overview"


def test_discover_candidates_unknown_entity_still_fails_on_unresponsive_search(
    db_session: Session,
) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="What is UnknownFramework and how does it work?",
        constraints={},
    )
    provider = FailingSearchProvider()
    service = _create_search_service(db_session, provider=provider)

    with pytest.raises(SearchProviderError) as error_info:
        service.discover_candidates(task.id)

    details = error_info.value.details
    assert details["known_path_fallback_applied"] is False
    assert details["known_path_fallback_candidate_count"] == 0
    assert details["query_count_attempted"] == 1
    assert details["empty_query_count"] == 1
    assert details["provider_error_reason"] == "searxng_empty_results_with_unresponsive_engines"
    assert details["failed_queries"][0]["query_text"] == (
        "What is UnknownFramework and how does it work?"
    )


def test_discover_candidates_dedupes_langgraph_known_path_guardrails_from_provider_results(
    db_session: Session,
) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="What is LangGraph and how does it work?",
        constraints={},
    )
    provider = StaticSearchProvider(
        responses={
            "What is LangGraph and how does it work?": (
                SearchResultItem(
                    url="https://docs.langchain.com/oss/python/langgraph/overview",
                    title="LangGraph overview - Docs by LangChain",
                    snippet="Official docs",
                    source_engine="fake",
                    rank=1,
                ),
            )
        }
    )
    service = _create_search_service(db_session, provider=provider)

    result = service.discover_candidates(task.id, include_default_expansions=False)
    canonical_urls = [
        candidate.canonical_url
        for candidate in CandidateUrlRepository(db_session).list_for_task(task.id)
    ]

    assert canonical_urls.count("https://docs.langchain.com/oss/python/langgraph/overview") == 1
    assert "https://github.com/langchain-ai/langgraph" in canonical_urls
    assert "https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md" in (
        canonical_urls
    )
    assert "https://raw.githubusercontent.com/langchain-ai/langgraph/master/README.md" in (
        canonical_urls
    )
    assert len(canonical_urls) == 10
    assert result.search_queries[1].duplicates_skipped >= 1
    fallback_candidates = [
        candidate
        for candidate in CandidateUrlRepository(db_session).list_for_task(task.id)
        if candidate.metadata_json.get("candidate_source") == "known_path_guardrail"
    ]
    assert len(fallback_candidates) == 1


def test_discover_candidates_generates_authoritative_sources_for_technical_project(
    db_session: Session,
) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="Explain FastAPI request handling and dependency injection",
        constraints={},
    )
    provider = StaticSearchProvider(responses={})
    service = _create_search_service(db_session, provider=provider)

    result = service.discover_candidates(task.id, include_default_expansions=False)
    persisted_queries = SearchQueryRepository(db_session).list_for_task(task.id)
    canonical_urls = [
        candidate.canonical_url
        for candidate in CandidateUrlRepository(db_session).list_for_task(task.id)
    ]

    assert result.search_queries[0].search_query.provider == "authoritative-source-resolver"
    assert "https://fastapi.tiangolo.com/" in canonical_urls
    assert "https://github.com/fastapi/fastapi" in canonical_urls
    assert "https://pypi.org/project/fastapi" in canonical_urls
    resolver_payload = _raw_response(persisted_queries[0])["known_source_resolver"]
    assert resolver_payload["selected_count"] >= 3
    assert "official_docs" in resolver_payload["source_classes"]
    assert "official_repository" in resolver_payload["source_classes"]


def test_discover_candidates_rejects_noisy_specialty_results_without_entity_match(
    db_session: Session,
) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="What is PyTorch and how does autograd work?",
        constraints={},
    )
    provider = StaticSearchProvider(
        responses={
            "What is PyTorch and how does autograd work?": (
                SearchResultItem(
                    url="https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Overview",
                    title="HTTP overview",
                    snippet="A web platform overview for HTTP.",
                    source_engine="mdn",
                    rank=1,
                ),
                SearchResultItem(
                    url="https://pytorch.org/docs/stable/autograd.html",
                    title="PyTorch autograd documentation",
                    snippet="PyTorch automatic differentiation package.",
                    source_engine="duckduckgo",
                    rank=2,
                ),
            )
        }
    )
    service = _create_search_service(db_session, provider=provider)

    service.discover_candidates(task.id, include_default_expansions=False)
    persisted_queries = SearchQueryRepository(db_session).list_for_task(task.id)
    canonical_urls = [
        candidate.canonical_url
        for candidate in CandidateUrlRepository(db_session).list_for_task(task.id)
    ]

    assert "https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Overview" not in (
        canonical_urls
    )
    assert "https://pytorch.org/docs/stable/autograd.html" in canonical_urls
    provider_payload = _raw_response(persisted_queries[1])["provider_result_diagnostics"]
    assert provider_payload["selected_count"] == 0
    assert provider_payload["rejected_noisy_count"] == 1
    assert provider_payload["rejected_results"][0]["source_engine"] == "mdn"


def test_discover_candidates_records_timeout_without_aborting_when_authoritative_sources_exist(
    db_session: Session,
) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="What is Kubernetes and how does scheduling work?",
        constraints={},
    )
    timeout_error = SearchProviderError(
        reason="searxng_timeout",
        message="SearXNG request timed out",
        status_code=None,
        content_type=None,
        body_preview=None,
        unresponsive_engines=[],
    )
    provider = SequencedSearchProvider(
        responses={}, failures={"Kubernetes scheduling": timeout_error}
    )
    service = _create_search_service(db_session, provider=provider)

    result = service.discover_candidates(
        task.id,
        planned_search_queries=[
            PlannedSearchQuery(
                query_text="Kubernetes scheduling",
                rationale="scheduler docs",
                expected_source_type="official_docs",
                priority=1,
            )
        ],
        include_default_expansions=False,
    )
    persisted_queries = SearchQueryRepository(db_session).list_for_task(task.id)
    canonical_urls = [
        candidate.canonical_url
        for candidate in CandidateUrlRepository(db_session).list_for_task(task.id)
    ]

    assert result.candidate_urls
    assert "https://kubernetes.io/docs/concepts/scheduling-eviction/kube-scheduler" in (
        canonical_urls
    )
    failure_payload = _raw_response(persisted_queries[1])["search_provider_failure"]
    assert failure_payload["provider_error_reason"] == "searxng_timeout"
    assert failure_payload["search_provider_failure_tolerated"] is True
    assert failure_payload["available_candidate_count"] >= 3


def test_discover_candidates_injects_searxng_docker_known_path_guardrails(
    db_session: Session,
) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="How to deploy SearXNG with Docker?",
        constraints={},
    )
    provider = StaticSearchProvider(
        responses={
            "How to deploy SearXNG with Docker?": (
                SearchResultItem(
                    url="https://docs.searxng.org/admin/installation-docker",
                    title="Installation container - SearXNG Documentation",
                    snippet="Official container docs",
                    source_engine="fake",
                    rank=1,
                ),
            )
        }
    )
    service = _create_search_service(db_session, provider=provider)

    result = service.discover_candidates(task.id, include_default_expansions=False)
    canonical_urls = [
        candidate.canonical_url
        for candidate in CandidateUrlRepository(db_session).list_for_task(task.id)
    ]

    assert canonical_urls.count("https://docs.searxng.org/admin/installation-docker") == 1
    assert "https://github.com/searxng/searxng-docker" in canonical_urls
    assert "https://github.com/searxng/searxng-docker/blob/master/docker-compose.yaml" not in (
        canonical_urls
    )
    assert (
        "https://raw.githubusercontent.com/searxng/searxng-docker/main/README.md" in canonical_urls
    )
    assert (
        "https://raw.githubusercontent.com/searxng/searxng-docker/master/README.md"
        in canonical_urls
    )
    assert (
        "https://raw.githubusercontent.com/searxng/searxng/master/container/docker-compose.yml"
        in canonical_urls
    )
    assert (
        "https://raw.githubusercontent.com/searxng/searxng/master/container/.env.example"
        in canonical_urls
    )
    assert result.search_queries[0].duplicates_skipped == 1


def test_discover_candidates_adds_raw_readme_for_github_repository_result(
    db_session: Session,
) -> None:
    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="How to deploy SearXNG with Docker?",
        constraints={},
    )
    provider = StaticSearchProvider(
        responses={
            "How to deploy SearXNG with Docker?": (
                SearchResultItem(
                    url="https://github.com/searxng/searxng-docker",
                    title="searxng/searxng-docker",
                    snippet="Official repository",
                    source_engine="fake",
                    rank=3,
                ),
            )
        }
    )
    service = _create_search_service(db_session, provider=provider)

    service.discover_candidates(task.id, include_default_expansions=False)
    canonical_urls = [
        candidate.canonical_url
        for candidate in CandidateUrlRepository(db_session).list_for_task(task.id)
    ]

    raw_main = "https://raw.githubusercontent.com/searxng/searxng-docker/main/README.md"
    raw_master = "https://raw.githubusercontent.com/searxng/searxng-docker/master/README.md"
    repository_html = "https://github.com/searxng/searxng-docker"

    assert set(canonical_urls[:2]) == {raw_main, raw_master}
    assert canonical_urls[2] == repository_html


def test_merge_official_github_raw_readme_derivatives_technical_explanation() -> None:
    base = [
        {
            "url": "https://github.com/langchain-ai/langgraph",
            "title": "langgraph",
            "snippet": "repo",
            "rank": 10016,
            "reason": "known_path",
            "source_role": "official_repository",
            "target_slots": ["official_sources", "key_features"],
        }
    ]
    merged = _merge_official_github_raw_readme_derivatives(
        list(base),
        query="What is LangGraph and how does it work?",
        constraints={},
    )
    urls = [row["url"] for row in merged]
    assert "https://github.com/langchain-ai/langgraph" in urls
    assert "https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md" in urls
    assert "https://raw.githubusercontent.com/langchain-ai/langgraph/master/README.md" in urls
    assert not any("src/" in str(u) for u in urls)
    deriv = [
        row
        for row in merged
        if row.get("official_repository_readme_derivative") is True
    ]
    assert len(deriv) == 2
    assert deriv[0]["derived_from_repository_url"] == "https://github.com/langchain-ai/langgraph"
    assert deriv[0]["source_intent"] == "official_repository_readme"
    assert deriv[0]["technical_slot_targets"] == ["official_sources", "key_features"]


def test_merge_official_github_raw_readme_skips_non_technical_query() -> None:
    base = [
        {
            "url": "https://github.com/langchain-ai/langgraph",
            "title": "langgraph",
            "snippet": "repo",
            "rank": 10,
            "reason": "known_path",
            "source_role": "official_repository",
        }
    ]
    merged = _merge_official_github_raw_readme_derivatives(
        list(base),
        query="How to deploy LangGraph with Docker?",
        constraints={},
    )
    assert len(merged) == 1


def test_merge_official_github_raw_readme_skips_issues_and_non_official() -> None:
    base = [
        {
            "url": "https://github.com/langchain-ai/langgraph/issues/1",
            "title": "issue",
            "snippet": "x",
            "rank": 1,
            "reason": "x",
            "source_role": "official_repository",
        },
        {
            "url": "https://github.com/random/unrelated",
            "title": "other",
            "snippet": "x",
            "rank": 2,
            "reason": "x",
            "source_role": "secondary_reference",
        },
    ]
    merged = _merge_official_github_raw_readme_derivatives(
        list(base),
        query="What is LangGraph and how does it work?",
        constraints={},
    )
    assert len(merged) == 2


def test_add_known_path_merges_readme_into_existing_duplicate(db_session: Session) -> None:
    from datetime import UTC, datetime

    task_service = create_research_task_service(db_session)
    task = task_service.create_task(
        query="What is LangGraph and how does it work?",
        constraints={"domains_allow": ["raw.githubusercontent.com", "github.com"]},
    )
    run = ResearchRunRepository(db_session).add(
        ResearchRun(
            task_id=task.id,
            round_no=1,
            current_state="PLANNED",
            checkpoint_json={},
        )
    )
    sq = SearchQueryRepository(db_session).add(
        SearchQuery(
            task_id=task.id,
            run_id=run.id,
            query_text=task.query,
            provider="searxng",
            round_no=1,
            issued_at=datetime.now(UTC),
            raw_response_json={},
        )
    )
    raw_main = "https://raw.githubusercontent.com/langchain-ai/langgraph/main/README.md"
    cand_repo = CandidateUrlRepository(db_session)
    existing = cand_repo.add(
        CandidateUrl(
            task_id=task.id,
            search_query_id=sq.id,
            original_url=raw_main,
            canonical_url=raw_main,
            domain="raw.githubusercontent.com",
            title="README",
            rank=50,
            selected=False,
            metadata_json={"provider": "searxng", "snippet": "Plain provider hit"},
        )
    )
    db_session.commit()
    existing_set = {raw_main}
    known_path: dict[str, object] = {
        "url": raw_main,
        "title": "README merged",
        "snippet": "derived",
        "rank": 2,
        "reason": "derivative",
        "source_role": "official_repository",
        "source_intent": "official_repository_readme",
        "technical_slot_targets": ["key_features"],
        "target_slots": ["key_features"],
        "derived_from_repository_url": "https://github.com/langchain-ai/langgraph",
        "official_repository_readme_derivative": True,
    }
    result = _add_known_path_candidates(
        task=task,
        search_query=sq,
        known_path_candidates=[known_path],
        existing_candidates=existing_set,
        candidate_url_repository=cand_repo,
        provider="searxng",
        query_text=task.query,
        expansion_kind="test",
        expansion_metadata={},
        candidate_source="test",
        fallback_reason=None,
    )
    db_session.commit()
    db_session.refresh(existing)
    assert result.added == 0
    assert result.duplicates_skipped == 0
    assert result.metadata_merged == 1
    md = existing.metadata_json or {}
    assert md.get("official_repository_readme_derivative") is True
    assert md.get("source_intent") == "official_repository_readme"
    assert md.get("technical_slot_targets") == ["key_features"]
    assert existing.rank == 2
