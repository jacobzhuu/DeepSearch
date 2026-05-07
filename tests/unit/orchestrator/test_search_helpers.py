from __future__ import annotations

import httpx
import pytest

from services.orchestrator.app.search import (
    SearchProviderError,
    SearchRequest,
    SearXNGSearchProvider,
    SimpleQueryExpansionStrategy,
    SmokeSearchProvider,
    YaCySearchProvider,
    canonicalize_url,
    is_domain_allowed,
)
from services.orchestrator.app.search import providers as provider_module


def test_canonicalize_url_normalizes_tracking_and_trailing_slash() -> None:
    canonical = canonicalize_url(" HTTPS://Exämple.com:443/a/../b/?utm_source=x&b=2&a=1#frag ")

    assert canonical is not None
    assert canonical.original_url == "HTTPS://Exämple.com:443/a/../b/?utm_source=x&b=2&a=1#frag"
    assert canonical.domain == "xn--exmple-cua.com"
    assert canonical.canonical_url == "https://xn--exmple-cua.com/b?a=1&b=2"


def test_canonicalize_url_unwraps_known_redirect_urls() -> None:
    canonical = canonicalize_url(
        "https://www.google.com/url?q=https%3A%2F%2FExample.com%2Fdocs%2F%3Futm_medium%3Dx"
    )

    assert canonical is not None
    assert canonical.original_url.startswith("https://www.google.com/url")
    assert canonical.domain == "example.com"
    assert canonical.canonical_url == "https://example.com/docs"


def test_is_domain_allowed_applies_allow_and_deny_lists_to_subdomains() -> None:
    assert is_domain_allowed(
        "news.example.com",
        allow_domains=("example.com",),
        deny_domains=(),
    )
    assert not is_domain_allowed(
        "blocked.example.com",
        allow_domains=("example.com",),
        deny_domains=("blocked.example.com",),
    )
    assert not is_domain_allowed(
        "other.example.net",
        allow_domains=("example.com",),
        deny_domains=(),
    )


def test_query_expansion_adds_base_and_site_queries_from_allow_list() -> None:
    strategy = SimpleQueryExpansionStrategy(max_domain_expansions=2)

    expanded_queries = strategy.expand(
        "  nvidia open model stack  ",
        constraints={
            "domains_allow": [
                "Example.com",
                "docs.example.com",
                "example.com",
                "third.example.com",
            ],
        },
    )

    assert [item.query_text for item in expanded_queries] == [
        "nvidia open model stack",
        "site:example.com nvidia open model stack",
        "site:docs.example.com nvidia open model stack",
    ]
    assert [item.expansion_kind for item in expanded_queries] == ["base", "site", "site"]


def test_searxng_provider_parses_results_and_tracks_request_params() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.url.params["q"] == "nvidia open model"
        assert request.url.params["format"] == "json"
        assert request.url.params["language"] == "zh-CN"
        assert request.url.params["engines"] == "google,bing"
        return httpx.Response(
            200,
            json={
                "number_of_results": 2,
                "query_correction": ["nvidia open source model"],
                "unresponsive_engines": [["duckduckgo", "timeout"]],
                "results": [
                    {
                        "url": "https://example.com/a?utm_source=x&id=1",
                        "title": "Example A",
                        "content": "Snippet A",
                        "engine": "google",
                        "category": "general",
                        "publishedDate": "2026-04-20",
                        "score": 12.5,
                    },
                    {
                        "url": "https://example.com/b",
                        "title": "Example B",
                        "content": "Snippet B",
                        "engine": "bing",
                        "category": "general",
                        "publishedDate": None,
                        "score": 8.4,
                    },
                ],
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = SearXNGSearchProvider(
        base_url="http://searxng.test",
        timeout_seconds=5.0,
        client=client,
    )

    response = provider.search(
        SearchRequest(
            query_text="nvidia open model",
            language="zh-CN",
            limit=10,
            source_engines=("google", "bing"),
        )
    )

    assert response.provider == "searxng"
    assert response.source_engines == ("bing", "google")
    assert response.result_count == 2
    assert [item.rank for item in response.results] == [1, 2]
    assert response.results[0].metadata["category"] == "general"
    assert response.metadata["request_params"]["engines"] == "google,bing"
    assert response.metadata["number_of_results"] == 2
    assert response.metadata["unresponsive_engines"] == ["duckduckgo"]


def test_searxng_provider_rejects_html_response() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html><body>not json</body></html>",
                request=request,
            )
        )
    )
    provider = SearXNGSearchProvider(
        base_url="http://searxng.test",
        timeout_seconds=5.0,
        client=client,
    )

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(SearchRequest(query_text="openai", language=None, limit=10))

    assert exc_info.value.reason == "searxng_html_response"
    assert exc_info.value.status_code == 200
    assert exc_info.value.content_type == "text/html; charset=utf-8"
    assert "not json" in (exc_info.value.body_preview or "")


def test_yacy_provider_parses_json_results() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/yacysearch.json"
        assert request.url.params["query"] == "LangGraph"
        assert request.url.params["maximumRecords"] == "5"
        return httpx.Response(
            200,
            json={
                "channels": [
                    {
                        "items": [
                            {
                                "link": "https://docs.langchain.com/oss/python/langgraph/overview",
                                "title": "LangGraph overview",
                                "description": "Official documentation.",
                            }
                        ]
                    }
                ]
            },
            request=request,
        )

    provider = YaCySearchProvider(
        base_url="http://yacy.test",
        timeout_seconds=5.0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    response = provider.search(SearchRequest(query_text="LangGraph", language=None, limit=5))

    assert response.provider == "yacy"
    assert response.result_count == 1
    assert response.results[0].source_engine == "yacy"
    assert response.results[0].title == "LangGraph overview"


def test_yacy_provider_wraps_read_timeout_as_search_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = YaCySearchProvider(
        base_url="http://yacy.test",
        timeout_seconds=5.0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(SearchRequest(query_text="LangGraph", language=None, limit=5))

    assert exc_info.value.reason == "yacy_timeout"
    assert exc_info.value.status_code is None
    assert exc_info.value.details["request_params"]["query"] == "LangGraph"


def test_yacy_provider_wraps_request_error_as_search_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    provider = YaCySearchProvider(
        base_url="http://yacy.test",
        timeout_seconds=5.0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(SearchRequest(query_text="LangGraph", language=None, limit=5))

    assert exc_info.value.reason == "yacy_request_error"
    assert exc_info.value.status_code is None
    assert exc_info.value.details["request_params"]["query"] == "LangGraph"


def test_searxng_provider_rejects_403_response() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                403,
                headers={"content-type": "text/plain"},
                text="Forbidden",
                request=request,
            )
        )
    )
    provider = SearXNGSearchProvider(
        base_url="http://searxng.test",
        timeout_seconds=5.0,
        client=client,
    )

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(SearchRequest(query_text="openai", language=None, limit=10))

    assert exc_info.value.reason == "searxng_http_forbidden"
    assert exc_info.value.status_code == 403
    assert "Forbidden" in (exc_info.value.body_preview or "")


def test_searxng_provider_rejects_empty_results_with_unresponsive_engines() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "results": [],
                    "unresponsive_engines": [
                        ["google", "CAPTCHA"],
                        {"engine": "bing", "error": "too many requests"},
                    ],
                },
                request=request,
            )
        )
    )
    provider = SearXNGSearchProvider(
        base_url="http://searxng.test",
        timeout_seconds=5.0,
        client=client,
    )

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(SearchRequest(query_text="openai", language=None, limit=10))

    assert exc_info.value.reason == "searxng_empty_results_with_unresponsive_engines"
    assert exc_info.value.unresponsive_engines == ["google", "bing"]


def test_searxng_provider_retries_empty_unresponsive_general_search_with_resilient_engines() -> (
    None
):
    seen_queries: list[httpx.URL] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_queries.append(request.url)
        if "engines=" not in str(request.url):
            return httpx.Response(
                200,
                json={
                    "results": [],
                    "unresponsive_engines": [["duckduckgo", "CAPTCHA"]],
                },
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "number_of_results": 1,
                "results": [
                    {
                        "url": "https://github.com/example/project",
                        "title": "example/project",
                        "content": "Repository result.",
                        "engine": "github",
                        "category": "it",
                    }
                ],
                "unresponsive_engines": [],
            },
            request=request,
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = SearXNGSearchProvider(
        base_url="http://searxng.test",
        timeout_seconds=5.0,
        client=client,
    )

    response = provider.search(SearchRequest(query_text="Example project", language=None, limit=10))

    assert len(seen_queries) == 2
    assert response.result_count == 1
    assert response.source_engines == ("github",)
    assert response.metadata["fallback_after_provider_error"]["reason"] == (
        "searxng_empty_results_with_unresponsive_engines"
    )
    assert "github" in response.metadata["fallback_source_engines"]
    assert "mdn" not in response.metadata["fallback_source_engines"]
    assert "stackoverflow" not in response.metadata["fallback_source_engines"]


def test_searxng_provider_wraps_read_timeout_as_search_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = SearXNGSearchProvider(
        base_url="http://searxng.test",
        timeout_seconds=5.0,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(SearchProviderError) as exc_info:
        provider.search(SearchRequest(query_text="Kubernetes scheduling", language=None, limit=5))

    assert exc_info.value.reason == "searxng_timeout"
    assert exc_info.value.status_code is None
    assert exc_info.value.details["request_params"]["q"] == "Kubernetes scheduling"


def test_searxng_provider_disables_environment_proxy_lookup_for_internal_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            del args
            captured.update(kwargs)

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
            del exc_type, exc, tb

        def get(self, url: str, params: dict[str, str | int]) -> httpx.Response:
            request = httpx.Request("GET", url, params=params)
            return httpx.Response(200, json={"results": []}, request=request)

    monkeypatch.setattr(provider_module.httpx, "Client", FakeClient)

    provider = SearXNGSearchProvider(
        base_url="http://searxng.test",
        timeout_seconds=5.0,
    )
    response = provider.search(
        SearchRequest(
            query_text="proxy-safe search",
            language=None,
            limit=5,
        )
    )

    assert response.result_count == 0
    assert captured["trust_env"] is False


def test_smoke_search_provider_marks_results_as_development_only() -> None:
    provider = SmokeSearchProvider()

    response = provider.search(
        SearchRequest(
            query_text="any research query",
            language=None,
            limit=10,
        )
    )

    assert response.provider == "smoke-search"
    assert response.result_count >= 3
    assert response.results[0].url.startswith("https://deepsearch-smoke.local/")
    assert response.results[0].metadata["real_search"] is False
    assert response.results[0].metadata["synthetic_fixture"] is True
    assert response.metadata["smoke_mode"] is True
