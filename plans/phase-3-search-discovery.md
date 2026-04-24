# Phase 3 Search Discovery And Candidate URL Intake

## 1. Objective

Introduce the first search-discovery slice of the orchestrator by adding a minimal search-provider abstraction, a SearXNG-backed implementation, query expansion, URL canonicalization, domain allow/deny filtering, and persistence of `search_query` and `candidate_url` records through thin API and service paths.

## 2. Why this exists

Phase 2 established task lifecycle, revision tracking, and stable event ordering, but the platform still cannot discover evidence sources. Phase 3 needs a narrow, reversible search-intake layer so the ledger can start recording search provenance before any fetch, parse, index, claim, or reporting behavior is introduced.

## 3. Scope

### In scope

- add a minimal search-provider interface plus a SearXNG implementation
- add a minimal query-expansion strategy
- canonicalize URLs before candidate intake
- apply domain allow/deny filtering before candidate persistence
- persist `search_query` and `candidate_url` through the existing ledger
- add thin repository helpers for search discovery reads
- add thin API routes for search execution and persisted search discovery reads
- add unit and API tests for canonicalization, provider parsing, repositories, services, and routes
- update the matching docs and the active plan

### Out of scope

- fetch execution, `fetch_job`, or `fetch_attempt` runtime behavior
- crawler, Playwright, Tika, or OpenSearch integration
- claim drafting, verification, or report generation
- worker scheduling, queues, or LangGraph execution
- search-result scoring beyond basic provider rank and metadata capture

## 4. Constraints

- stay strictly within Phase 3
- keep task-state behavior unchanged except for search-specific guards
- do not introduce fetch, parse, index, claim, or report side effects
- canonicalize before dedupe
- keep the provider interface extensible for later acquisition policy work
- prefer additive API changes and avoid breaking the Phase 2 task contracts
- add a production `httpx` dependency only if required for the SearXNG client

## 5. Relevant files and systems

- `services/orchestrator/app/settings.py`
- `services/orchestrator/app/main.py`
- `services/orchestrator/app/api/routes/`
- `services/orchestrator/app/api/schemas/`
- `services/orchestrator/app/services/`
- `services/orchestrator/app/search/`
- `packages/db/models/ledger.py`
- `packages/db/repositories/search.py`
- `packages/db/repositories/research.py`
- `tests/unit/db/`
- `tests/unit/orchestrator/`
- `services/orchestrator/tests/`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/phases/phase-3.md`
- `pyproject.toml`

## 6. Milestones

### Milestone 1
- intent: establish the Phase 3 search-discovery contract and implementation seams
- code changes: new plan, search provider abstraction, query expansion, canonicalization helpers, and settings
- validation: unit tests for canonicalization and query expansion

### Milestone 2
- intent: persist search discovery into the ledger without introducing fetch or execution semantics
- code changes: repository helpers plus a search-discovery service that creates or reuses the current run, persists `search_query`, filters and dedupes candidates, and exposes the persisted reads
- validation: repository and service tests

### Milestone 3
- intent: expose the minimal API surface for search discovery
- code changes: search execution and read routes, request and response schemas, dependency wiring, and API tests
- validation: API tests plus a narrow manual flow against a local fake SearXNG response server

### Milestone 4
- intent: keep the operator-facing docs synchronized
- code changes: update API, schema, runbook, architecture, and phase docs
- validation: manual doc-to-code comparison

## 7. Implementation log

- 2026-04-23 research: reread the repository instructions, current Phase 2 code, and the Phase 3 requirements from the product spec. Existing `search_query` and `candidate_url` columns appear sufficient for the minimum contract, so Phase 3 starts from a no-migration assumption unless implementation proves otherwise.
- 2026-04-23 milestone 1: added the `services/orchestrator/app/search/` package, a minimal search-provider interface, a SearXNG-backed implementation, query expansion, URL canonicalization, and the Phase 3 settings. This kept the acquisition seam small and synchronous.
- 2026-04-23 milestone 2: added task-scoped search repositories and a `SearchDiscoveryService` that creates or reuses revision-scoped runs, persists `search_query`, canonicalizes and filters URLs, and dedupes candidates before persistence. No fetch jobs or task events were introduced.
- 2026-04-23 milestone 3: added thin search discovery APIs for execute, list search queries, and list candidate URLs. Added repository, helper, service, and API tests for ordering, dedupe, domain filtering, run rollover after `revise`, and paused-task guards.
- 2026-04-23 milestone 4: updated `docs/api.md`, `docs/schema.md`, `docs/runbook.md`, `docs/architecture.md`, and added `docs/phases/phase-3.md` to keep operator-facing behavior aligned with the code.

## 8. Validation

- completed:
  - `python3 -m pytest tests/unit/orchestrator/test_search_helpers.py tests/unit/orchestrator/test_search_discovery_service.py services/orchestrator/tests/test_search_discovery_api.py tests/unit/db/test_repositories.py -q`
  - `python3 -m ruff check services/orchestrator/app/search services/orchestrator/app/services/search_discovery.py services/orchestrator/app/api/routes/search_discovery.py services/orchestrator/app/api/schemas/search_discovery.py tests/unit/orchestrator/test_search_helpers.py tests/unit/orchestrator/test_search_discovery_service.py services/orchestrator/tests/test_search_discovery_api.py tests/unit/db/test_repositories.py`
  - `python3 -m black --check services/orchestrator/app/search services/orchestrator/app/services/search_discovery.py services/orchestrator/app/api/routes/search_discovery.py services/orchestrator/app/api/schemas/search_discovery.py tests/unit/orchestrator/test_search_helpers.py tests/unit/orchestrator/test_search_discovery_service.py services/orchestrator/tests/test_search_discovery_api.py tests/unit/db/test_repositories.py`
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit`
  - `python3 -m pytest`
  - `rm -f /tmp/deepresearch_phase3_manual.db && DATABASE_URL=sqlite:////tmp/deepresearch_phase3_manual.db python3 -m alembic -c alembic.ini upgrade head`
  - manual API flow against a temporary SQLite database and a local fake SearXNG server on `http://127.0.0.1:18080`, including `GET /healthz`, `GET /readyz`, `POST /api/v1/research/tasks`, `POST /api/v1/research/tasks/{task_id}/searches`, `GET /api/v1/research/tasks/{task_id}/search-queries`, and `GET /api/v1/research/tasks/{task_id}/candidate-urls?domain=example.com&selected=false`

- known unvalidated areas:
  - live SearXNG interoperability against a real deployed instance
  - PostgreSQL-specific behavior because the current environment has no PostgreSQL service
  - Docker-based validation because `docker` is unavailable in the current environment

## 9. Risks and unknowns

- the current schema does not model many-to-many provenance between one canonical URL and multiple search queries, so Phase 3 may need to keep the first persisted origin only and document that limit
- minimal canonicalization must be conservative enough not to merge distinct URLs incorrectly
- search discovery should not silently bypass task pause or cancel semantics
- a later phase may need a `search_query_candidate_url` style association table so one canonical URL can be explicitly linked to every `search_query` that surfaced it

## 10. Rollback / recovery

- revert the Phase 3 search files, repository helpers, routes, schemas, tests, and docs
- no schema migration was added, so rollback remains code-only in this phase

## 11. Deferred work

- fetch-job creation and acquisition policy
- search retries, rate limiting, or backoff orchestration
- provider fan-out beyond SearXNG
- search-result scoring and source-quality enrichment
