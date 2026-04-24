from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ExpandedQuery:
    query_text: str
    expansion_kind: str
    metadata: dict[str, Any] = field(default_factory=dict)


class QueryExpansionStrategy(Protocol):
    def expand(self, query: str, *, constraints: dict[str, Any]) -> list[ExpandedQuery]: ...


class SimpleQueryExpansionStrategy:
    def __init__(self, *, max_domain_expansions: int) -> None:
        self.max_domain_expansions = max_domain_expansions

    def expand(self, query: str, *, constraints: dict[str, Any]) -> list[ExpandedQuery]:
        normalized_query = query.strip()
        if not normalized_query:
            return []

        expanded_queries = [
            ExpandedQuery(
                query_text=normalized_query,
                expansion_kind="base",
            )
        ]

        seen_query_texts = {normalized_query}
        allowed_domains = _unique_domains(constraints.get("domains_allow", []))
        for domain in allowed_domains[: self.max_domain_expansions]:
            query_text = f"site:{domain} {normalized_query}"
            if query_text in seen_query_texts:
                continue

            expanded_queries.append(
                ExpandedQuery(
                    query_text=query_text,
                    expansion_kind="site",
                    metadata={"domain": domain},
                )
            )
            seen_query_texts.add(query_text)

        return expanded_queries


def _unique_domains(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []

    unique_domains: list[str] = []
    seen_domains: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        normalized = value.strip().lower().lstrip(".")
        if not normalized or normalized in seen_domains:
            continue
        unique_domains.append(normalized)
        seen_domains.add(normalized)

    return unique_domains
