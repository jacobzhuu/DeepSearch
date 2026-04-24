from __future__ import annotations

import httpx
import pytest

from services.orchestrator.app.search import (
    SearchRequest,
    SearXNGSearchProvider,
    SimpleQueryExpansionStrategy,
    SmokeSearchProvider,
    canonicalize_url,
    is_domain_allowed,
)
from services.orchestrator.app.search import providers as provider_module


def test_canonicalize_url_normalizes_and_preserves_non_root_trailing_slash() -> None:
    canonical = canonicalize_url(" HTTPS://Exämple.com:443/a/../b/?utm_source=x&b=2&a=1#frag ")

    assert canonical is not None
    assert canonical.original_url == "HTTPS://Exämple.com:443/a/../b/?utm_source=x&b=2&a=1#frag"
    assert canonical.domain == "xn--exmple-cua.com"
    assert canonical.canonical_url == "https://xn--exmple-cua.com/b/?a=1&b=2"


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
    assert response.result_count == 1
    assert response.results[0].url == "https://example.com/"
    assert response.results[0].metadata["real_search"] is False
    assert response.metadata["smoke_mode"] is True
