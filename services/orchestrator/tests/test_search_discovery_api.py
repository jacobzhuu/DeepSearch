from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from services.orchestrator.app.api.routes.search_discovery import get_search_provider
from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.main import create_app
from services.orchestrator.app.search import SearchRequest, SearchResponse, SearchResultItem


class FakeSearchProvider:
    name = "searxng"

    def __init__(self) -> None:
        self.requests: list[SearchRequest] = []

    def search(self, request: SearchRequest) -> SearchResponse:
        self.requests.append(request)
        results: tuple[SearchResultItem, ...]
        if request.query_text == "Search API task":
            results = (
                SearchResultItem(
                    url="https://example.com/report?utm_source=x&id=1",
                    title="Report",
                    snippet="Base result",
                    source_engine="google",
                    rank=1,
                ),
                SearchResultItem(
                    url="https://blocked.example.com/internal",
                    title="Blocked",
                    snippet=None,
                    source_engine="google",
                    rank=2,
                ),
                SearchResultItem(
                    url="https://example.com/appendix/",
                    title="Appendix",
                    snippet="Second result",
                    source_engine="bing",
                    rank=3,
                ),
            )
        elif request.query_text == "site:example.com Search API task":
            results = (
                SearchResultItem(
                    url="https://example.com/report?id=1",
                    title="Report duplicate",
                    snippet="Duplicate",
                    source_engine="google",
                    rank=1,
                ),
            )
        else:
            results = ()

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


@pytest.fixture()
def client(session_factory: sessionmaker[Session]) -> Generator[TestClient, None, None]:
    app = create_app()
    provider = FakeSearchProvider()

    def override_db_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_search_provider] = lambda: provider

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_search_discovery_endpoints_persist_queries_and_candidates(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/research/tasks",
        json={
            "query": "Search API task",
            "constraints": {
                "domains_allow": ["example.com"],
                "domains_deny": ["blocked.example.com"],
                "language": "zh-CN",
            },
        },
    )
    task_id = create_response.json()["task_id"]

    discover_response = client.post(f"/api/v1/research/tasks/{task_id}/searches")
    search_queries_response = client.get(f"/api/v1/research/tasks/{task_id}/search-queries")
    candidate_urls_response = client.get(
        f"/api/v1/research/tasks/{task_id}/candidate-urls",
        params={"domain": "example.com", "selected": False},
    )

    assert discover_response.status_code == 201
    assert discover_response.json()["round_no"] == 1
    assert discover_response.json()["revision_no"] == 1
    assert len(discover_response.json()["search_queries"]) == 2
    assert discover_response.json()["candidate_urls_added"] == 2
    assert discover_response.json()["duplicates_skipped"] == 1
    assert discover_response.json()["filtered_out"] == 1

    assert search_queries_response.status_code == 200
    assert [item["query_text"] for item in search_queries_response.json()["search_queries"]] == [
        "Search API task",
        "site:example.com Search API task",
    ]
    assert search_queries_response.json()["search_queries"][0]["metadata"]["task_revision_no"] == 1

    assert candidate_urls_response.status_code == 200
    assert [item["canonical_url"] for item in candidate_urls_response.json()["candidate_urls"]] == [
        "https://example.com/report?id=1",
        "https://example.com/appendix/",
    ]
    assert (
        candidate_urls_response.json()["candidate_urls"][0]["metadata"]["source_engine"] == "google"
    )


def test_search_discovery_endpoint_rejects_paused_task(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/research/tasks",
        json={
            "query": "Paused API task",
            "constraints": {"domains_allow": ["example.com"]},
        },
    )
    task_id = create_response.json()["task_id"]

    client.post(f"/api/v1/research/tasks/{task_id}/pause")
    discover_response = client.post(f"/api/v1/research/tasks/{task_id}/searches")

    assert discover_response.status_code == 409
    assert "cannot discover search candidates" in discover_response.json()["detail"]
