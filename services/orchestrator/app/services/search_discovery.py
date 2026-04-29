from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from packages.db.models import CandidateUrl, ResearchRun, ResearchTask, SearchQuery
from packages.db.repositories import (
    CandidateUrlRepository,
    ResearchRunRepository,
    ResearchTaskRepository,
    SearchQueryRepository,
)
from services.orchestrator.app.planning import PlannedSearchQuery
from services.orchestrator.app.search import (
    ExpandedQuery,
    QueryExpansionStrategy,
    SearchProvider,
    SearchRequest,
    canonicalize_url,
    is_domain_allowed,
)
from services.orchestrator.app.services.research_tasks import (
    PHASE2_ACTIVE_STATUS,
    TaskNotFoundError,
)


class SearchDiscoveryConflictError(Exception):
    def __init__(self, task_id: UUID, current_status: str) -> None:
        super().__init__(
            f"cannot discover search candidates for task {task_id} from status {current_status}"
        )
        self.task_id = task_id
        self.current_status = current_status


@dataclass(frozen=True)
class PersistedSearchQuery:
    search_query: SearchQuery
    candidates_added: int
    duplicates_skipped: int
    filtered_out: int


@dataclass(frozen=True)
class SearchDiscoveryResult:
    task: ResearchTask
    run: ResearchRun
    search_queries: list[PersistedSearchQuery]
    candidate_urls: list[CandidateUrl]
    duplicates_skipped: int
    filtered_out: int


class SearchDiscoveryService:
    def __init__(
        self,
        session: Session,
        *,
        task_repository: ResearchTaskRepository,
        run_repository: ResearchRunRepository,
        search_query_repository: SearchQueryRepository,
        candidate_url_repository: CandidateUrlRepository,
        search_provider: SearchProvider,
        query_expansion_strategy: QueryExpansionStrategy,
        max_results_per_query: int,
        allowed_statuses: tuple[str, ...] = (PHASE2_ACTIVE_STATUS,),
    ) -> None:
        self.session = session
        self.task_repository = task_repository
        self.run_repository = run_repository
        self.search_query_repository = search_query_repository
        self.candidate_url_repository = candidate_url_repository
        self.search_provider = search_provider
        self.query_expansion_strategy = query_expansion_strategy
        self.max_results_per_query = max_results_per_query
        self.allowed_statuses = allowed_statuses

    def discover_candidates(
        self,
        task_id: UUID,
        *,
        planned_search_queries: list[PlannedSearchQuery] | None = None,
    ) -> SearchDiscoveryResult:
        task = self._get_task(task_id)
        if task.status not in self.allowed_statuses:
            raise SearchDiscoveryConflictError(task.id, task.status)

        constraints = dict(task.constraints_json)
        expanded_queries = self._expand_queries(
            task.query,
            constraints=constraints,
            planned_search_queries=planned_search_queries,
        )
        if not expanded_queries:
            raise ValueError(f"task {task.id} does not have a valid searchable query")

        run = self._get_or_create_current_run(task)
        existing_candidates = {
            candidate.canonical_url
            for candidate in self.candidate_url_repository.list_for_task(task.id)
        }
        discovered_candidates: list[CandidateUrl] = []
        persisted_queries: list[PersistedSearchQuery] = []
        duplicates_skipped = 0
        filtered_out = 0
        remaining_slots = _resolve_total_candidate_limit(
            constraints.get("max_urls"),
            default_limit=self.max_results_per_query * len(expanded_queries),
        )

        for expanded_query in expanded_queries:
            if remaining_slots <= 0:
                break

            request_limit = min(self.max_results_per_query, remaining_slots)
            provider_response = self.search_provider.search(
                SearchRequest(
                    query_text=expanded_query.query_text,
                    language=_resolve_language(constraints),
                    limit=request_limit,
                    source_engines=_resolve_source_engines(constraints),
                )
            )

            search_query = self.search_query_repository.add(
                SearchQuery(
                    task_id=task.id,
                    run_id=run.id,
                    query_text=expanded_query.query_text,
                    provider=provider_response.provider,
                    round_no=run.round_no,
                    issued_at=datetime.now(UTC),
                    raw_response_json={
                        "task_revision_no": task.revision_no,
                        "expansion_kind": expanded_query.expansion_kind,
                        "expansion_metadata": expanded_query.metadata,
                        "source_engines": list(provider_response.source_engines),
                        "response_metadata": provider_response.metadata,
                        "result_count": provider_response.result_count,
                    },
                )
            )

            added_for_query = 0
            duplicates_for_query = 0
            filtered_for_query = 0
            for result in provider_response.results:
                canonical = canonicalize_url(result.url)
                if canonical is None:
                    filtered_for_query += 1
                    continue
                if not is_domain_allowed(
                    canonical.domain,
                    allow_domains=_resolve_domains(constraints.get("domains_allow")),
                    deny_domains=_resolve_domains(constraints.get("domains_deny")),
                ):
                    filtered_for_query += 1
                    continue
                if canonical.canonical_url in existing_candidates:
                    duplicates_for_query += 1
                    continue

                candidate = self.candidate_url_repository.add(
                    CandidateUrl(
                        task_id=task.id,
                        search_query_id=search_query.id,
                        original_url=canonical.original_url,
                        canonical_url=canonical.canonical_url,
                        domain=canonical.domain,
                        title=result.title,
                        rank=result.rank,
                        selected=False,
                        metadata_json={
                            "provider": provider_response.provider,
                            "source_engine": result.source_engine,
                            "snippet": result.snippet,
                            "result_metadata": result.metadata,
                            "task_revision_no": task.revision_no,
                            "expansion_kind": expanded_query.expansion_kind,
                            "expansion_metadata": expanded_query.metadata,
                            "query_text": expanded_query.query_text,
                        },
                    )
                )
                discovered_candidates.append(candidate)
                existing_candidates.add(canonical.canonical_url)
                added_for_query += 1
                remaining_slots -= 1
                if remaining_slots <= 0:
                    break

            known_path_candidates = _known_path_candidates_for_query(
                query=task.query,
                provider_results=provider_response.results,
                constraints=constraints,
            )
            for known_path in known_path_candidates:
                canonical = canonicalize_url(known_path["url"])
                if canonical is None:
                    filtered_for_query += 1
                    continue
                if canonical.canonical_url in existing_candidates:
                    duplicates_for_query += 1
                    continue
                candidate = self.candidate_url_repository.add(
                    CandidateUrl(
                        task_id=task.id,
                        search_query_id=search_query.id,
                        original_url=canonical.original_url,
                        canonical_url=canonical.canonical_url,
                        domain=canonical.domain,
                        title=known_path["title"],
                        rank=int(known_path["rank"]),
                        selected=False,
                        metadata_json={
                            "provider": provider_response.provider,
                            "source_engine": "deterministic_known_path",
                            "snippet": known_path["snippet"],
                            "result_metadata": {
                                "known_path_candidate": True,
                                "known_path_reason": known_path["reason"],
                            },
                            "task_revision_no": task.revision_no,
                            "expansion_kind": expanded_query.expansion_kind,
                            "expansion_metadata": expanded_query.metadata,
                            "query_text": expanded_query.query_text,
                            "known_path_candidate": True,
                            "source_selection_reason": known_path["reason"],
                        },
                    )
                )
                discovered_candidates.append(candidate)
                existing_candidates.add(canonical.canonical_url)
                added_for_query += 1

            duplicates_skipped += duplicates_for_query
            filtered_out += filtered_for_query
            persisted_queries.append(
                PersistedSearchQuery(
                    search_query=search_query,
                    candidates_added=added_for_query,
                    duplicates_skipped=duplicates_for_query,
                    filtered_out=filtered_for_query,
                )
            )

        self.session.commit()
        return SearchDiscoveryResult(
            task=task,
            run=run,
            search_queries=persisted_queries,
            candidate_urls=discovered_candidates,
            duplicates_skipped=duplicates_skipped,
            filtered_out=filtered_out,
        )

    def list_search_queries(self, task_id: UUID) -> list[SearchQuery]:
        self._get_task(task_id)
        return self.search_query_repository.list_for_task(task_id)

    def list_candidate_urls(
        self,
        task_id: UUID,
        *,
        domain: str | None = None,
        selected: bool | None = None,
        limit: int | None = None,
    ) -> list[CandidateUrl]:
        self._get_task(task_id)
        return self.candidate_url_repository.list_for_task(
            task_id,
            domain=domain,
            selected=selected,
            limit=limit,
        )

    def _get_task(self, task_id: UUID) -> ResearchTask:
        task = self.task_repository.get(task_id)
        if task is None:
            raise TaskNotFoundError(task_id)
        return task

    def _get_or_create_current_run(self, task: ResearchTask) -> ResearchRun:
        latest_run = self.run_repository.get_latest_for_task(task.id)
        if latest_run is not None and _run_revision_no(latest_run) == task.revision_no:
            return latest_run

        next_round_no = 1 if latest_run is None else latest_run.round_no + 1

        return self.run_repository.add(
            ResearchRun(
                task_id=task.id,
                round_no=next_round_no,
                current_state=task.status,
                checkpoint_json={
                    "task_revision_no": task.revision_no,
                    "phase": "search_discovery",
                },
            )
        )

    def _expand_queries(
        self,
        query: str,
        *,
        constraints: dict[str, Any],
        planned_search_queries: list[PlannedSearchQuery] | None,
    ) -> list[ExpandedQuery]:
        base_expanded = self.query_expansion_strategy.expand(query, constraints=constraints)
        if not planned_search_queries:
            return base_expanded

        expanded_queries: list[ExpandedQuery] = []
        seen_query_texts: set[str] = set()
        for planned_query in sorted(planned_search_queries, key=lambda item: item.priority):
            query_text = planned_query.query_text.strip()
            if not query_text or query_text in seen_query_texts:
                continue
            expanded_queries.append(
                ExpandedQuery(
                    query_text=query_text,
                    expansion_kind="research_plan",
                    metadata={
                        "rationale": planned_query.rationale,
                        "expected_source_type": planned_query.expected_source_type,
                        "priority": planned_query.priority,
                        "query_source": planned_query.query_source,
                    },
                )
            )
            seen_query_texts.add(query_text)

        for expanded_query in base_expanded:
            if expanded_query.query_text in seen_query_texts:
                continue
            expanded_queries.append(expanded_query)
            seen_query_texts.add(expanded_query.query_text)

        return expanded_queries


def create_search_discovery_service(
    session: Session,
    *,
    search_provider: SearchProvider,
    query_expansion_strategy: QueryExpansionStrategy,
    max_results_per_query: int,
    allowed_statuses: tuple[str, ...] = (PHASE2_ACTIVE_STATUS,),
) -> SearchDiscoveryService:
    return SearchDiscoveryService(
        session,
        task_repository=ResearchTaskRepository(session),
        run_repository=ResearchRunRepository(session),
        search_query_repository=SearchQueryRepository(session),
        candidate_url_repository=CandidateUrlRepository(session),
        search_provider=search_provider,
        query_expansion_strategy=query_expansion_strategy,
        max_results_per_query=max_results_per_query,
        allowed_statuses=allowed_statuses,
    )


def _resolve_language(constraints: dict[str, Any]) -> str | None:
    language = constraints.get("language")
    if isinstance(language, str) and language.strip():
        return language.strip()
    return None


def _resolve_source_engines(constraints: dict[str, Any]) -> tuple[str, ...]:
    engines = constraints.get("source_engines")
    if engines is None:
        engines = constraints.get("search_engines")
    if not isinstance(engines, list):
        return ()

    normalized_engines: list[str] = []
    seen_engines: set[str] = set()
    for item in engines:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if not normalized or normalized in seen_engines:
            continue
        normalized_engines.append(normalized)
        seen_engines.add(normalized)
    return tuple(normalized_engines)


def _resolve_domains(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    normalized_domains: list[str] = []
    seen_domains: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip().lower().lstrip(".")
        if not normalized or normalized in seen_domains:
            continue
        normalized_domains.append(normalized)
        seen_domains.add(normalized)
    return tuple(normalized_domains)


def _resolve_total_candidate_limit(raw_limit: Any, *, default_limit: int) -> int:
    if isinstance(raw_limit, int) and raw_limit > 0:
        return raw_limit
    return default_limit


def _known_path_candidates_for_query(
    *,
    query: str,
    provider_results: tuple[Any, ...],
    constraints: dict[str, Any],
) -> list[dict[str, object]]:
    if not _is_searxng_overview_query(query):
        return []

    saw_searxng_official = False
    for result in provider_results:
        canonical = canonicalize_url(result.url)
        if canonical is None:
            continue
        normalized_domain = canonical.domain.removeprefix("www.")
        if normalized_domain in {"searxng.org", "docs.searxng.org"}:
            saw_searxng_official = True
            break
    if not saw_searxng_official:
        return []

    allow_domains = _resolve_domains(constraints.get("domains_allow"))
    deny_domains = _resolve_domains(constraints.get("domains_deny"))
    candidates = [
        {
            "url": "https://docs.searxng.org/user/about.html",
            "title": "SearXNG about",
            "snippet": "Deterministic known overview path for SearXNG documentation.",
            "rank": 10001,
            "reason": "known_path_candidate: official about page for SearXNG overview query",
        },
        {
            "url": "https://en.wikipedia.org/wiki/SearXNG",
            "title": "SearXNG - Wikipedia",
            "snippet": "Deterministic stable reference candidate for SearXNG overview query.",
            "rank": 10002,
            "reason": "known_path_candidate: Wikipedia reference for SearXNG overview query",
        },
    ]
    filtered: list[dict[str, object]] = []
    for candidate in candidates:
        canonical = canonicalize_url(str(candidate["url"]))
        if canonical is None:
            continue
        if not is_domain_allowed(
            canonical.domain,
            allow_domains=allow_domains,
            deny_domains=deny_domains,
        ):
            continue
        filtered.append(candidate)
    return filtered


def _is_searxng_overview_query(query: str) -> bool:
    lower = query.lower()
    return "searxng" in lower and (
        "what is" in lower
        or "overview" in lower
        or "how does" in lower
        or "how it works" in lower
        or lower.startswith("explain ")
    )


def _run_revision_no(run: ResearchRun) -> int | None:
    value = run.checkpoint_json.get("task_revision_no")
    return value if isinstance(value, int) else None
