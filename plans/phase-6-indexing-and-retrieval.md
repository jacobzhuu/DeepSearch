# Phase 6 Indexing And Retrieval Over Source Chunks

## 1. Objective

Implement the first indexing and retrieval slice on top of persisted `source_document` and `source_chunk` rows: abstract a chunk-index backend, provide an OpenSearch-backed implementation, write source chunks into the index with traceable identifiers, and expose minimal debug APIs for indexed-chunk inspection and task-scoped retrieval.

## 2. Why this exists

Phase 5 can parse snapshots into stable source chunks, but the platform still cannot query those chunks efficiently. Phase 6 needs a narrow, reversible indexing and retrieval seam so later claim, verification, and reporting phases can build on indexed evidence rather than rescanning database rows each time.

## 3. Scope

### In scope

- add a chunk-index backend abstraction
- implement an OpenSearch REST-backed index backend without introducing a new production dependency
- validate unsupported index backend configuration at app startup without requiring live connectivity
- index `source_chunk` rows with traceable fields:
  - `task_id`
  - `source_document_id`
  - `source_chunk_id`
  - `canonical_url`
  - `domain`
  - `chunk_no`
  - `text`
  - `metadata`
- implement a minimal indexing service for task-scoped source chunks
- implement a minimal retrieval service for task-scoped chunk lookup with basic paging
- add thin debug APIs for indexing, viewing indexed chunks, and retrieval results
- codify current parse reason enums and current-state `source_document` semantics in docs
- add unit, service, API, and narrow manual validation
- update docs and this plan

### Out of scope

- claim drafting, citation-span binding, verification, and report generation
- complex reranking, semantic embeddings, hybrid search, or cross-task retrieval
- worker scheduling, async indexing pipelines, or retry orchestration
- schema changes unless implementation proves a DB seam is strictly necessary
- Tika, browser fetch, or OpenSearch dashboards setup

## 4. Constraints

- stay strictly within Phase 6
- keep task, search, acquisition, and parsing semantics from earlier phases compatible
- do not introduce a new production dependency if existing `httpx` can handle OpenSearch REST calls
- index writes must be traceable to `source_chunk`
- retrieval should favor stability and explainability over sophistication
- keep debug APIs thin and explicit; no hidden “research answer” facade
- avoid schema changes unless the indexing path truly cannot be modeled without one
- keep backend misconfiguration as a startup-validation failure, but do not make app startup depend on live OpenSearch reachability

## 5. Relevant files and systems

- `services/orchestrator/app/settings.py`
- `services/orchestrator/app/main.py`
- `services/orchestrator/app/indexing/`
- `services/orchestrator/app/services/`
- `services/orchestrator/app/api/routes/`
- `services/orchestrator/app/api/schemas/`
- `packages/db/repositories/sources.py`
- `packages/db/models/ledger.py`
- `tests/unit/orchestrator/`
- `services/orchestrator/tests/`
- `.env.example`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/architecture.md`
- `docs/phases/phase-6.md`

## 6. Milestones

### Milestone 1

- intent: establish the index backend seam and startup-time configuration validation
- expected code changes: new plan, settings, backend abstraction, OpenSearch REST implementation, app startup validation
- expected validation: backend unit tests and startup validation tests

### Milestone 2

- intent: index persisted source chunks into the backend in a task-scoped and traceable way
- expected code changes: repository helpers, indexing service, write/list retrieval models, parse-reason constants if needed for documentation stability
- expected validation: repository and service tests

### Milestone 3

- intent: expose minimal operator-facing indexing and retrieval APIs
- expected code changes: debug routes, request/response schemas, dependency wiring, API tests
- expected validation: API tests plus a narrow manual flow against fake SearXNG and fake OpenSearch services

### Milestone 4

- intent: keep operator-facing docs synchronized with Phase 6 behavior
- expected code changes: update architecture, API, schema, runbook, phase doc, and this plan
- expected validation: manual doc-to-code comparison

## 7. Implementation log

- 2026-04-23 research: reread `AGENTS.md`, `deep_research_codex_dev_spec.md`, `PLANS.md`, `code_review.md`, and the current docs. Confirmed Phase 6 should stop at chunk indexing and minimal task-scoped retrieval, with no claim, verification, citation binding, or reporting work.
- 2026-04-23 implementation: added a chunk-index backend seam under `services/orchestrator/app/indexing/`, implemented the first OpenSearch REST backend on top of `httpx`, validated backend configuration during app startup, and kept the backend seam free of live-connectivity checks so local startup remains deterministic.
- 2026-04-23 implementation: added `IndexingService`, repository support for task-scoped `source_chunk` selection by ids, and thin debug APIs for `POST /index`, `GET /indexed-chunks`, and `GET /retrieve`. Retrieval remains a simple task-scoped text match with stable tie-break ordering.
- 2026-04-23 implementation: documented that `source_document` is current-state rather than versioned history, and promoted parse result `reason` values to a stable enum so API consumers do not treat them as arbitrary strings.
- 2026-04-23 validation: targeted backend, repository, service, API, startup, and parsing tests passed. Full lint, type, full pytest, and narrow manual validation then passed in the same turn; the manual API chain covered task create, search, fetch, parse, index, indexed-chunk inspection, and retrieval against temporary SQLite plus fake SearXNG and fake OpenSearch services.

## 8. Validation

- planned:
  - `python3 -m ruff check .`
  - `python3 -m black --check .`
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit`
  - `python3 -m pytest`
  - narrow manual API flow covering task create, search, fetch, parse, index, indexed-chunk list, and retrieval against temporary SQLite plus fake SearXNG and fake OpenSearch services

- completed so far:
  - `python3 -m pytest tests/unit/orchestrator/test_indexing_backend.py tests/unit/orchestrator/test_indexing_service.py services/orchestrator/tests/test_indexing_api.py tests/unit/orchestrator/test_app_startup.py tests/unit/orchestrator/test_parsing_service.py tests/unit/db/test_repositories.py -q` — passed
  - `python3 -m pytest tests/unit/db/test_repositories.py -q` — passed
  - `python3 -m ruff check .` — passed
  - `python3 -m black --check .` — passed
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit` — passed
  - `python3 -m pytest` — passed (`64 passed`)
  - manual API chain with fake SearXNG on `127.0.0.1:18080`, fake OpenSearch on `127.0.0.1:19200`, SQLite at `/tmp/deepresearch_phase6_manual.db`, and filesystem snapshots at `/tmp/deepresearch_phase6_snapshots` — passed

- known unvalidated areas before implementation:
  - live OpenSearch interoperability against a real cluster
  - PostgreSQL-specific behavior because the current environment has no PostgreSQL service
  - Docker-based validation because `docker` is unavailable in the current environment

## 9. Risks and unknowns

- the OpenSearch REST surface needs to stay minimal enough for local fake-server validation while still matching real cluster semantics closely enough to be useful
- retrieval scoring should be simple and interpretable, but not so strict that no chunks are ever returned for ordinary queries
- current `source_document` rows are current-state records, not a version chain; docs must keep that explicit so Phase 6 retrieval is not mistaken for versioned evidence lookup
- index writes are external side effects, so the idempotency boundary needs to remain the deterministic `source_chunk_id` document id
- there is still no relational index-job ledger table; Phase 6 intentionally keeps indexing state external to the database

## 10. Rollback / recovery

- revert the Phase 6 indexing files, routes, schemas, tests, and docs
- if a migration is introduced later in implementation, document the exact downgrade command and data-shape caveats before finishing the turn

## 11. Deferred work

- embeddings, hybrid retrieval, and reranking
- retrieval tied to claim drafting or verification
- background indexing workers and retries
- versioned parse-history-aware retrieval
- reporting over retrieved chunks
