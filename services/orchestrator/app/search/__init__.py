"""Search discovery helpers for the orchestrator."""

from services.orchestrator.app.search.canonicalization import (
    CanonicalUrl,
    canonicalize_url,
    is_domain_allowed,
)
from services.orchestrator.app.search.providers import (
    SearchProvider,
    SearchProviderError,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    SearXNGSearchProvider,
    SmokeSearchProvider,
)
from services.orchestrator.app.search.query_expansion import (
    ExpandedQuery,
    QueryExpansionStrategy,
    SimpleQueryExpansionStrategy,
)

__all__ = [
    "CanonicalUrl",
    "ExpandedQuery",
    "QueryExpansionStrategy",
    "SearchProvider",
    "SearchProviderError",
    "SearchRequest",
    "SearchResponse",
    "SearchResultItem",
    "SearXNGSearchProvider",
    "SimpleQueryExpansionStrategy",
    "SmokeSearchProvider",
    "canonicalize_url",
    "is_domain_allowed",
]
