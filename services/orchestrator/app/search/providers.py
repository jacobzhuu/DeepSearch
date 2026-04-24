from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx


@dataclass(frozen=True)
class SearchRequest:
    query_text: str
    language: str | None
    limit: int
    source_engines: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    time_range: str | None = None


@dataclass(frozen=True)
class SearchResultItem:
    url: str
    title: str | None
    snippet: str | None
    source_engine: str | None
    rank: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResponse:
    provider: str
    source_engines: tuple[str, ...]
    result_count: int
    results: tuple[SearchResultItem, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


class SearchProvider(Protocol):
    name: str

    def search(self, request: SearchRequest) -> SearchResponse: ...


class SmokeSearchProvider:
    name = "smoke-search"

    def search(self, request: SearchRequest) -> SearchResponse:
        del request
        return SearchResponse(
            provider=self.name,
            source_engines=("development-smoke",),
            result_count=1,
            results=(
                SearchResultItem(
                    url="https://example.com/",
                    title="Development smoke source: Example Domain",
                    snippet=(
                        "Development smoke result. This is not real web search evidence; "
                        "use SEARCH_PROVIDER=searxng for real search."
                    ),
                    source_engine="development-smoke",
                    rank=1,
                    metadata={
                        "smoke_mode": True,
                        "real_search": False,
                    },
                ),
            ),
            metadata={
                "smoke_mode": True,
                "real_search": False,
                "warning": "development smoke search provider; not real search",
            },
        )


class SearXNGSearchProvider:
    name = "searxng"

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.client = client

    def search(self, request: SearchRequest) -> SearchResponse:
        request_params: dict[str, str | int] = {
            "q": request.query_text,
            "format": "json",
        }
        if request.language is not None:
            request_params["language"] = request.language
        if request.source_engines:
            request_params["engines"] = ",".join(request.source_engines)
        if request.categories:
            request_params["categories"] = ",".join(request.categories)
        if request.time_range is not None:
            request_params["time_range"] = request.time_range

        payload = self._perform_request(request_params)
        raw_results = payload.get("results", [])
        if not isinstance(raw_results, list):
            raw_results = []

        parsed_results: list[SearchResultItem] = []
        for index, raw_result in enumerate(raw_results[: request.limit], start=1):
            if not isinstance(raw_result, dict):
                continue
            url = raw_result.get("url")
            if not isinstance(url, str) or not url.strip():
                continue

            title = raw_result.get("title")
            snippet = raw_result.get("content")
            source_engine = raw_result.get("engine")
            parsed_results.append(
                SearchResultItem(
                    url=url,
                    title=title if isinstance(title, str) else None,
                    snippet=snippet if isinstance(snippet, str) else None,
                    source_engine=source_engine if isinstance(source_engine, str) else None,
                    rank=index,
                    metadata={
                        "category": raw_result.get("category"),
                        "published_date": raw_result.get("publishedDate"),
                        "score": raw_result.get("score"),
                    },
                )
            )

        discovered_engines = tuple(
            sorted(
                {
                    result.source_engine
                    for result in parsed_results
                    if result.source_engine is not None and result.source_engine.strip()
                }
            )
        )

        return SearchResponse(
            provider=self.name,
            source_engines=discovered_engines,
            result_count=len(parsed_results),
            results=tuple(parsed_results),
            metadata={
                "request_params": request_params,
                "number_of_results": payload.get("number_of_results"),
                "query_correction": payload.get("query_correction"),
            },
        )

    def _perform_request(self, params: dict[str, str | int]) -> dict[str, Any]:
        if self.client is not None:
            response = self.client.get(f"{self.base_url}/search", params=params)
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {}

        with httpx.Client(timeout=self.timeout_seconds, trust_env=False) as client:
            response = client.get(f"{self.base_url}/search", params=params)
            response.raise_for_status()
            payload = response.json()
            return payload if isinstance(payload, dict) else {}
