from __future__ import annotations

import re
from dataclasses import dataclass, field
from json import JSONDecodeError
from typing import Any, Protocol

import httpx

from packages.observability import get_logger

logger = get_logger(__name__)
SEARXNG_RESILIENT_FALLBACK_ENGINES: tuple[str, ...] = (
    "wikipedia",
    "github",
    "arxiv",
    "pypi",
    "semantic scholar",
)

# site: filters are a Web-search idiom honoured by general engines (Brave,
# Google, DuckDuckGo, ...). The resilient fallback only hits academic / code
# engines (Wikipedia, arXiv, Semantic Scholar, GitHub, PyPI) which do not
# honour `site:` and treat it as literal text -- this degrades the retry into
# matching the literal token "site" against unrelated abstracts. Strip these
# operators before issuing the fallback request so the academic engines see a
# clean topical query, and record the strip so callers can see what changed.
_SITE_OPERATOR_PATTERN = re.compile(r"(?:^|\s)-?site:\S+", re.IGNORECASE)


def _strip_site_operators(query_text: str) -> tuple[str, tuple[str, ...]]:
    """Return ``(cleaned_query, stripped_operators)``.

    The cleaned query is collapsed-whitespace; ``stripped_operators`` is the
    tuple of raw matches in order (without leading whitespace). When no
    operators are found the original query is returned unchanged.
    """

    matches = _SITE_OPERATOR_PATTERN.findall(query_text)
    if not matches:
        return query_text, ()
    stripped = _SITE_OPERATOR_PATTERN.sub(" ", query_text)
    cleaned = re.sub(r"\s+", " ", stripped).strip()
    return cleaned, tuple(match.strip() for match in matches)


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


class SearchProviderError(RuntimeError):
    def __init__(
        self,
        *,
        reason: str,
        message: str,
        status_code: int | None,
        content_type: str | None,
        body_preview: str | None,
        unresponsive_engines: list[str],
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.status_code = status_code
        self.content_type = content_type
        self.body_preview = body_preview
        self.unresponsive_engines = unresponsive_engines
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "message": str(self),
            "status": self.status_code,
            "content_type": self.content_type,
            "body_preview": self.body_preview,
            "unresponsive_engines": self.unresponsive_engines,
        }


class SmokeSearchProvider:
    name = "smoke-search"

    def search(self, request: SearchRequest) -> SearchResponse:
        topic = _smoke_topic_for_query(request.query_text)
        result_specs = _smoke_result_specs_for_query(request.query_text)
        results = tuple(
            SearchResultItem(
                url=f"https://deepsearch-smoke.local/{topic.slug}/{spec.path}",
                title=f"Synthetic development smoke source: {topic.label} {spec.title}",
                snippet=spec.snippet,
                source_engine="development-smoke",
                rank=index,
                metadata={
                    "smoke_mode": True,
                    "real_search": False,
                    "synthetic_fixture": True,
                    "fixture_topic": topic.slug,
                    "fixture_intent": spec.intent,
                },
            )
            for index, spec in enumerate(result_specs[: request.limit], start=1)
        )
        return SearchResponse(
            provider=self.name,
            source_engines=("development-smoke",),
            result_count=len(results),
            results=results,
            metadata={
                "smoke_mode": True,
                "real_search": False,
                "synthetic_fixture": True,
                "fixture_topic": topic.slug,
                "warning": (
                    "development smoke search provider; synthetic fixture, not real search"
                ),
            },
        )


@dataclass(frozen=True)
class _SmokeTopic:
    slug: str
    label: str


@dataclass(frozen=True)
class _SmokeResultSpec:
    path: str
    title: str
    snippet: str
    intent: str


_KNOWN_SMOKE_TOPICS: tuple[tuple[str, str], ...] = (
    ("searxng", "SearXNG"),
    ("opensearch", "OpenSearch"),
    ("langgraph", "LangGraph"),
    ("model context protocol", "Model Context Protocol"),
    ("mcp", "Model Context Protocol"),
    ("dify", "Dify"),
    ("retrieval-augmented generation", "Retrieval-Augmented Generation"),
    ("rag", "Retrieval-Augmented Generation"),
    ("chatgpt deep research", "ChatGPT Deep Research and Gemini Deep Research"),
    ("gemini deep research", "ChatGPT Deep Research and Gemini Deep Research"),
)


def _smoke_topic_for_query(query: str) -> _SmokeTopic:
    normalized_query = " ".join(query.split())
    lower = normalized_query.lower()
    if "brave search" in lower and "tavily" in lower:
        return _SmokeTopic(slug="ai-search-comparison", label="AI search agent tools")
    for marker, label in _KNOWN_SMOKE_TOPICS:
        if marker in lower:
            return _SmokeTopic(slug=_slugify(label), label=label)
    match = re.search(r"\bwhat\s+is\s+(.+?)(?:\s+and\s+how|\?|$)", normalized_query, re.I)
    if match is not None:
        label = match.group(1).strip()
        if label:
            return _SmokeTopic(slug=_slugify(label), label=label)
    return _SmokeTopic(slug="generic-research-topic", label="Generic research topic")


def _smoke_result_specs_for_query(query: str) -> tuple[_SmokeResultSpec, ...]:
    lower = query.lower()
    if "compare" in lower or "difference" in lower:
        return (
            _SmokeResultSpec(
                path="comparison",
                title="comparison overview",
                snippet="Synthetic comparison source with dimensions, tradeoffs, and source scope.",
                intent="comparison",
            ),
            _SmokeResultSpec(
                path="limitations",
                title="limitations and risks",
                snippet="Synthetic limitations source for deterministic smoke diagnostics.",
                intent="limitations",
            ),
            _SmokeResultSpec(
                path="overview",
                title="overview",
                snippet="Synthetic overview source for smoke-mode evidence extraction.",
                intent="overview",
            ),
        )
    if "docker" in lower or "deploy" in lower or "deployment" in lower:
        return (
            _SmokeResultSpec(
                path="deployment",
                title="deployment guide",
                snippet=(
                    "Synthetic deployment source with prerequisites, configuration, and caveats."
                ),
                intent="deployment",
            ),
            _SmokeResultSpec(
                path="overview",
                title="overview",
                snippet="Synthetic overview source for smoke-mode evidence extraction.",
                intent="overview",
            ),
            _SmokeResultSpec(
                path="limitations",
                title="limitations",
                snippet="Synthetic caveats source for deployment smoke diagnostics.",
                intent="limitations",
            ),
        )
    if "privacy" in lower or "limitation" in lower or "limitations" in lower:
        return (
            _SmokeResultSpec(
                path="privacy",
                title="privacy and trust model",
                snippet="Synthetic privacy source with advantages and limitations.",
                intent="privacy",
            ),
            _SmokeResultSpec(
                path="limitations",
                title="limitations",
                snippet="Synthetic limitations source for smoke-mode evidence extraction.",
                intent="limitations",
            ),
            _SmokeResultSpec(
                path="overview",
                title="overview",
                snippet="Synthetic overview source for smoke-mode evidence extraction.",
                intent="overview",
            ),
        )
    return (
        _SmokeResultSpec(
            path="overview",
            title="overview",
            snippet="Synthetic overview source with definition evidence for smoke-mode research.",
            intent="overview",
        ),
        _SmokeResultSpec(
            path="mechanism",
            title="mechanism",
            snippet="Synthetic mechanism source explaining how the topic works.",
            intent="mechanism",
        ),
        _SmokeResultSpec(
            path="limitations",
            title="limitations",
            snippet="Synthetic limitations source for smoke-mode coverage diagnostics.",
            intent="limitations",
        ),
    )


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "generic-research-topic"


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

        fallback_metadata: dict[str, Any] = {}
        try:
            payload = self._perform_request(request_params)
        except SearchProviderError as error:
            if not _should_retry_with_resilient_engines(error, request=request):
                raise
            cleaned_query, stripped_site_operators = _strip_site_operators(request.query_text)
            fallback_params = {
                **request_params,
                "engines": ",".join(SEARXNG_RESILIENT_FALLBACK_ENGINES),
            }
            if stripped_site_operators:
                fallback_params["q"] = cleaned_query
            try:
                payload = self._perform_request(fallback_params)
            except SearchProviderError as fallback_error:
                raise error from fallback_error
            request_params = fallback_params
            fallback_metadata = {
                "fallback_after_provider_error": error.to_payload(),
                "fallback_source_engines": list(SEARXNG_RESILIENT_FALLBACK_ENGINES),
            }
            if stripped_site_operators:
                fallback_metadata["fallback_query_rewrites"] = {
                    "original_query": request.query_text,
                    "fallback_query": cleaned_query,
                    "stripped_site_operators": list(stripped_site_operators),
                    "rewrite_reason": "site_operator_stripped_for_academic_only_fallback",
                }
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
                "unresponsive_engines": _normalize_unresponsive_engines(
                    payload.get("unresponsive_engines")
                ),
                **fallback_metadata,
            },
        )

    def _perform_request(self, params: dict[str, str | int]) -> dict[str, Any]:
        try:
            if self.client is not None:
                response = self.client.get(f"{self.base_url}/search", params=params)
                return self._validate_endpoint_response(response)

            with httpx.Client(timeout=self.timeout_seconds, trust_env=False) as client:
                response = client.get(f"{self.base_url}/search", params=params)
                return self._validate_endpoint_response(response)
        except httpx.TimeoutException as error:
            raise SearchProviderError(
                reason="searxng_timeout",
                message=f"SearXNG request timed out: {error}",
                status_code=None,
                content_type=None,
                body_preview=None,
                unresponsive_engines=[],
                details={"request_params": params},
            ) from error
        except httpx.RequestError as error:
            raise SearchProviderError(
                reason="searxng_request_error",
                message=f"SearXNG request failed: {error}",
                status_code=None,
                content_type=None,
                body_preview=None,
                unresponsive_engines=[],
                details={"request_params": params},
            ) from error

    def _validate_endpoint_response(self, response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        body_preview = _body_preview(response)
        unresponsive_engines: list[str] = []

        if response.status_code == 403:
            self._log_endpoint_response(
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=unresponsive_engines,
                level="warning",
            )
            raise SearchProviderError(
                reason="searxng_http_forbidden",
                message=(
                    "SearXNG returned HTTP 403 Forbidden. Check endpoint access, rate limits, "
                    "engine CAPTCHA, or reverse-proxy rules."
                ),
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=unresponsive_engines,
            )

        if response.status_code >= 400:
            self._log_endpoint_response(
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=unresponsive_engines,
                level="warning",
            )
            raise SearchProviderError(
                reason="searxng_http_error",
                message=f"SearXNG returned HTTP {response.status_code}.",
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=unresponsive_engines,
            )

        if _looks_like_html_response(content_type=content_type, body_preview=body_preview):
            self._log_endpoint_response(
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=unresponsive_engines,
                level="warning",
            )
            raise SearchProviderError(
                reason="searxng_html_response",
                message=(
                    "SearXNG endpoint returned HTML instead of JSON. Point SEARXNG_BASE_URL "
                    "at a SearXNG-compatible /search?format=json endpoint, not the web UI or "
                    "frontend server."
                ),
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=unresponsive_engines,
            )

        try:
            payload = response.json()
        except (JSONDecodeError, ValueError) as error:
            self._log_endpoint_response(
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=unresponsive_engines,
                level="warning",
            )
            raise SearchProviderError(
                reason="searxng_invalid_json",
                message=f"SearXNG response was not valid JSON: {error}",
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=unresponsive_engines,
            ) from error

        if not isinstance(payload, dict):
            self._log_endpoint_response(
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=unresponsive_engines,
                level="warning",
            )
            raise SearchProviderError(
                reason="searxng_invalid_json_shape",
                message="SearXNG JSON response was not an object.",
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=unresponsive_engines,
            )

        unresponsive_engines = _normalize_unresponsive_engines(payload.get("unresponsive_engines"))
        raw_results = payload.get("results")
        if unresponsive_engines and (not isinstance(raw_results, list) or not raw_results):
            self._log_endpoint_response(
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=unresponsive_engines,
                level="warning",
            )
            raise SearchProviderError(
                reason="searxng_empty_results_with_unresponsive_engines",
                message=(
                    "SearXNG returned no results and reported unresponsive engines: "
                    f"{', '.join(unresponsive_engines)}."
                ),
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=unresponsive_engines,
            )

        self._log_endpoint_response(
            status_code=response.status_code,
            content_type=content_type,
            body_preview=body_preview,
            unresponsive_engines=unresponsive_engines,
            level="warning" if unresponsive_engines else "info",
        )
        return payload

    def _log_endpoint_response(
        self,
        *,
        status_code: int,
        content_type: str,
        body_preview: str,
        unresponsive_engines: list[str],
        level: str,
    ) -> None:
        log_method = logger.warning if level == "warning" else logger.info
        log_method(
            "search.searxng.response",
            extra={
                "SEARCH_PROVIDER": self.name,
                "SEARXNG_BASE_URL": self.base_url,
                "status": status_code,
                "content_type": content_type,
                "body_preview": body_preview,
                "unresponsive_engines": unresponsive_engines,
            },
        )


class YaCySearchProvider:
    name = "yacy"

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        resource: str = "local",
        verify: str = "false",
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.resource = resource.strip() or "local"
        self.verify = verify.strip() or "false"
        self.client = client

    def search(self, request: SearchRequest) -> SearchResponse:
        query_text = request.query_text
        if request.language:
            query_text = f"{query_text} LANGUAGE:{request.language}"
        request_params: dict[str, str | int] = {
            "query": query_text,
            "contentdom": "text",
            "maximumRecords": max(1, request.limit),
            "startRecord": 0,
            "resource": self.resource,
            "verify": self.verify,
            "nav": "none",
        }
        payload = self._perform_request(request_params)
        parsed_results = _parse_yacy_results(payload, limit=request.limit)
        return SearchResponse(
            provider=self.name,
            source_engines=("yacy",),
            result_count=len(parsed_results),
            results=tuple(parsed_results),
            metadata={
                "request_params": request_params,
                "resource": self.resource,
                "verify": self.verify,
                "raw_result_shape": _yacy_result_shape(payload),
            },
        )

    def _perform_request(self, params: dict[str, str | int]) -> dict[str, Any]:
        try:
            if self.client is not None:
                response = self.client.get(f"{self.base_url}/yacysearch.json", params=params)
                return self._validate_endpoint_response(response)

            with httpx.Client(timeout=self.timeout_seconds, trust_env=False) as client:
                response = client.get(f"{self.base_url}/yacysearch.json", params=params)
                return self._validate_endpoint_response(response)
        except httpx.TimeoutException as error:
            raise SearchProviderError(
                reason="yacy_timeout",
                message=f"YaCy request timed out: {error}",
                status_code=None,
                content_type=None,
                body_preview=None,
                unresponsive_engines=[],
                details={"request_params": params},
            ) from error
        except httpx.RequestError as error:
            raise SearchProviderError(
                reason="yacy_request_error",
                message=f"YaCy request failed: {error}",
                status_code=None,
                content_type=None,
                body_preview=None,
                unresponsive_engines=[],
                details={"request_params": params},
            ) from error

    def _validate_endpoint_response(self, response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        body_preview = _body_preview(response)
        if response.status_code >= 400:
            raise SearchProviderError(
                reason="yacy_http_error",
                message=f"YaCy returned HTTP {response.status_code}.",
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=[],
            )
        if _looks_like_html_response(content_type=content_type, body_preview=body_preview):
            raise SearchProviderError(
                reason="yacy_html_response",
                message=(
                    "YaCy endpoint returned HTML instead of JSON. Point YACY_BASE_URL at a "
                    "YaCy server exposing /yacysearch.json."
                ),
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=[],
            )
        try:
            payload = response.json()
        except (JSONDecodeError, ValueError) as error:
            raise SearchProviderError(
                reason="yacy_invalid_json",
                message=f"YaCy response was not valid JSON: {error}",
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=[],
            ) from error
        if not isinstance(payload, dict):
            raise SearchProviderError(
                reason="yacy_invalid_json_shape",
                message="YaCy JSON response was not an object.",
                status_code=response.status_code,
                content_type=content_type,
                body_preview=body_preview,
                unresponsive_engines=[],
            )
        return payload


def _parse_yacy_results(payload: dict[str, Any], *, limit: int) -> list[SearchResultItem]:
    raw_items: list[Any] = []
    channels = payload.get("channels")
    if isinstance(channels, list) and channels:
        first_channel = channels[0]
        if isinstance(first_channel, dict) and isinstance(first_channel.get("items"), list):
            raw_items = first_channel["items"]
    if not raw_items and isinstance(payload.get("items"), list):
        raw_items = payload["items"]
    if not raw_items and isinstance(payload.get("results"), list):
        raw_items = payload["results"]

    parsed_results: list[SearchResultItem] = []
    for index, raw_item in enumerate(raw_items[:limit], start=1):
        if not isinstance(raw_item, dict):
            continue
        url = _first_string(raw_item, "link", "url", "href", "sku")
        if url is None:
            continue
        parsed_results.append(
            SearchResultItem(
                url=url,
                title=_first_string(raw_item, "title", "dc:title"),
                snippet=_first_string(raw_item, "description", "snippet", "content"),
                source_engine="yacy",
                rank=index,
                metadata={
                    "pub_date": _first_string(raw_item, "pubDate", "date", "lastModified"),
                    "size": raw_item.get("size"),
                    "ranking": raw_item.get("ranking"),
                },
            )
        )
    return parsed_results


def _first_string(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _yacy_result_shape(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("channels"), list):
        return "channels"
    if isinstance(payload.get("items"), list):
        return "items"
    if isinstance(payload.get("results"), list):
        return "results"
    return "unknown"


def _body_preview(response: httpx.Response) -> str:
    return response.text[:300]


def _looks_like_html_response(*, content_type: str, body_preview: str) -> bool:
    normalized_type = content_type.lower()
    if "text/html" in normalized_type:
        return True
    stripped_preview = body_preview.lstrip().lower()
    return stripped_preview.startswith("<!doctype html") or stripped_preview.startswith("<html")


def _normalize_unresponsive_engines(raw_value: Any) -> list[str]:
    if not isinstance(raw_value, list):
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_value:
        if isinstance(item, str):
            engine = item.strip()
        elif isinstance(item, list | tuple) and item:
            raw_engine = item[0]
            engine = raw_engine.strip() if isinstance(raw_engine, str) else ""
        elif isinstance(item, dict):
            raw_engine = item.get("engine") or item.get("name")
            engine = raw_engine.strip() if isinstance(raw_engine, str) else ""
        else:
            engine = ""

        if not engine or engine in seen:
            continue
        normalized.append(engine)
        seen.add(engine)
    return normalized


def _should_retry_with_resilient_engines(
    error: SearchProviderError,
    *,
    request: SearchRequest,
) -> bool:
    return (
        error.reason == "searxng_empty_results_with_unresponsive_engines"
        and not request.source_engines
        and not request.categories
    )
