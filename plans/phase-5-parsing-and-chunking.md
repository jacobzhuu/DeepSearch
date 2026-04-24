# Phase 5 Parsing And Chunking From Content Snapshots

## 1. Objective

Implement the first parsing slice on top of persisted `content_snapshot` rows: read stored snapshot bytes through the object-store abstraction, extract minimal body text from `text/html` and `text/plain`, persist one provenance-linked `source_document`, and persist stable `source_chunk` rows with a simple explainable chunking strategy.

## 2. Why this exists

Phase 4 can acquire raw response snapshots, but the platform still cannot turn those bytes into ledgered source text for later citation, retrieval, or claim work. Phase 5 needs a narrow parsing and chunking seam so downstream phases can build on stable `source_document` and `source_chunk` records instead of re-reading raw network responses.

## 3. Scope

### In scope

- add the minimum provenance-safe schema seam needed to trace parsed output back to `content_snapshot`
- extend the snapshot object-store abstraction so parsing can read stored bytes
- fail application startup early when snapshot backend configuration is unsupported
- implement minimal extractors for `text/html` and `text/plain`
- implement a stable, explainable chunking strategy and persist `source_chunk`
- add repositories and a parsing service for `source_document` and `source_chunk`
- add thin API routes for `POST /parse`, `GET /source-documents`, and `GET /source-chunks`
- record explicit skip reasons for unsupported MIME types and already-parsed snapshots in the parse response
- add unit, repository, service, API, migration, and narrow manual validation
- update docs and this plan

### Out of scope

- Tika, PDF parsing, Office parsing, or attachment discovery
- OpenSearch indexing or retrieval over chunks
- claim drafting, verification, or report generation
- browser fetching, worker scheduling, or retry orchestration
- advanced boilerplate removal, heuristic scoring, or semantic chunk optimization

## 4. Constraints

- stay strictly within Phase 5
- keep task and acquisition semantics from Phases 2 through 4 compatible
- do not introduce new production dependencies if the standard library is sufficient
- parsing results must be traceable to `content_snapshot`
- chunking should favor stability and operator explainability over sophistication
- only already-fetched successful snapshots are eligible for parsing
- unsupported MIME types must be skipped explicitly, not silently ignored
- if a migration is added, keep it minimal, reversible, and scoped to the parsing provenance path

## 5. Relevant files and systems

- `packages/db/models/ledger.py`
- `packages/db/repositories/fetch.py`
- `packages/db/repositories/sources.py`
- `migrations/versions/`
- `services/orchestrator/app/storage/`
- `services/orchestrator/app/services/`
- `services/orchestrator/app/api/routes/`
- `services/orchestrator/app/api/schemas/`
- `services/orchestrator/app/main.py`
- `services/orchestrator/app/settings.py`
- `tests/unit/db/`
- `tests/unit/orchestrator/`
- `services/orchestrator/tests/`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/architecture.md`
- `docs/phases/phase-5.md`

## 6. Milestones

### Milestone 1

- intent: establish provenance-safe parsing prerequisites
- expected code changes: plan file, minimal migration, ORM updates, snapshot object-store read support, startup backend validation
- expected validation: migration test, storage test, startup validation test

### Milestone 2

- intent: implement minimal extractors and parsing persistence
- expected code changes: HTML and plain-text extraction helpers, stable chunker, repository additions, parsing service
- expected validation: extractor tests, repository tests, service tests

### Milestone 3

- intent: expose the thin Phase 5 API
- expected code changes: parse and source-ledger routes, request and response schemas, dependency wiring, API tests
- expected validation: API tests plus a narrow manual flow using a temporary SQLite database and a real parseable snapshot

### Milestone 4

- intent: keep operator-facing docs synchronized with Phase 5 behavior
- expected code changes: update architecture, API, schema, runbook, phase doc, and this plan
- expected validation: doc-to-code comparison

## 7. Implementation log

- 2026-04-23 research: reread `AGENTS.md`, `deep_research_codex_dev_spec.md`, `PLANS.md`, `code_review.md`, and the current docs. Confirmed Phase 5 should stop at minimal parsing and chunking over existing `content_snapshot` rows, with no Tika, indexing, retrieval, claim, or report behavior.
- 2026-04-23 milestone 1: added migration `20260423_0004` for `source_document.content_snapshot_id`, extended the snapshot object-store abstraction with read support, and moved unsupported snapshot-backend failures to application startup.
- 2026-04-23 milestone 2: added minimal `text/html` and `text/plain` extractors, a stable `paragraph_window_v1` chunker, repository helpers for task-scoped source reads, and a `ParsingService` that creates or updates provenance-linked `source_document` plus `source_chunk`.
- 2026-04-23 milestone 3: added thin parsing routes and schemas for `POST /parse`, `GET /source-documents`, and `GET /source-chunks`, while keeping task status semantics and task-event behavior unchanged.
- 2026-04-23 milestone 4: updated `docs/api.md`, `docs/schema.md`, `docs/runbook.md`, `docs/architecture.md`, added `docs/phases/phase-5.md`, and formalized the acquisition policy boundary table in the docs.

## 8. Validation

- completed:
  - `python3 -m ruff check .`
  - `python3 -m black --check .`
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit`
  - `python3 -m pytest`
  - `DATABASE_URL=sqlite:////tmp/deepresearch_phase5_manual.db python3 -m alembic -c alembic.ini upgrade head`
  - `DATABASE_URL=sqlite:////tmp/deepresearch_phase5_manual.db python3 -m alembic -c alembic.ini downgrade 20260423_0003`
  - manual API flow against a temporary SQLite database, a fake local SearXNG server on `http://127.0.0.1:18080`, and the public HTTP target `https://example.com/`, covering `GET /healthz`, `GET /readyz`, `POST /api/v1/research/tasks`, `POST /api/v1/research/tasks/{task_id}/searches`, `POST /api/v1/research/tasks/{task_id}/fetches`, `POST /api/v1/research/tasks/{task_id}/parse`, `GET /api/v1/research/tasks/{task_id}/source-documents`, and `GET /api/v1/research/tasks/{task_id}/source-chunks`

- known unvalidated areas:
  - live MinIO interoperability because storage remains filesystem-backed in the current environment
  - PostgreSQL-specific behavior because the current environment has no PostgreSQL service
  - Docker-based validation because `docker` is unavailable in the current environment

## 9. Risks and unknowns

- the current schema does not yet model parse history, so a minimal provenance link must not overstate long-term versioning guarantees
- HTML extraction must be conservative enough to avoid obvious navigation noise without introducing hidden heuristics
- if one canonical URL is re-fetched later, the Phase 5 parser needs a clear minimum rule for reusing or updating `source_document`
- startup validation should fail early for unsupported snapshot backends without breaking existing test setup

## 10. Rollback / recovery

- revert the Phase 5 parsing files, schema changes, routes, schemas, tests, and docs
- if a migration is added, document the exact downgrade command and any data-shape caveats before finishing the turn

## 11. Deferred work

- Tika-backed document parsing and attachment handling
- richer boilerplate removal, language-aware tokenization, and semantic chunking
- indexing or retrieval APIs over `source_chunk`
- parse retry orchestration and worker execution
- full parse-history modeling beyond the minimum snapshot provenance seam
