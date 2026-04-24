# Phase 4 HTTP Acquisition And Content Snapshots

## 1. Objective

Implement the first acquisition slice on top of persisted `candidate_url` rows: create `fetch_job` and `fetch_attempt` records, execute a minimal policy-guarded HTTP fetch, persist raw response bytes through an object-store abstraction, and record `content_snapshot` metadata in the ledger.

## 2. Why this exists

Phase 3 can discover candidate URLs, but the platform still cannot turn those candidates into traceable evidence artifacts. Phase 4 needs a minimal, auditable acquisition chain so later parsing and indexing phases can build on stored response snapshots rather than directly on network side effects.

## 3. Scope

### In scope

- unify the checked-in spec filename references to `deep_research_codex_dev_spec.md`
- make the Phase 3 candidate provenance TODO explicit in docs and plans
- add a minimal acquisition policy for HTTP fetches
- add an object-store abstraction and a filesystem-backed implementation for snapshots
- add repository helpers for `fetch_job`, `fetch_attempt`, and `content_snapshot`
- add a minimal acquisition service and thin API routes
- add tests for policy checks, object storage, repositories, services, and APIs
- update docs and this plan

### Out of scope

- browser or Playwright fetching
- Tika parsing or attachment extraction
- OpenSearch indexing
- claim drafting, verification, or report generation
- worker-based scheduling or queue execution

## 4. Constraints

- stay strictly within Phase 4
- keep search discovery and task-state behavior compatible with Phases 2 and 3
- acquisition policy must be explicit in code and docs
- only `http` and `https` targets are allowed
- loopback, private, link-local, metadata, and other non-global targets must be blocked
- timeouts, redirect limits, and body-size limits must be bounded
- keep the object-store interface ready for a later MinIO implementation
- if a migration is added, keep it minimal and reversible

## 5. Relevant files and systems

- `AGENTS.md`
- `code_review.md`
- `services/orchestrator/app/settings.py`
- `services/orchestrator/app/main.py`
- `services/orchestrator/app/api/routes/`
- `services/orchestrator/app/api/schemas/`
- `services/orchestrator/app/services/`
- `services/orchestrator/app/search/`
- `services/orchestrator/app/acquisition/`
- `services/orchestrator/app/storage/`
- `packages/db/models/ledger.py`
- `packages/db/repositories/fetch.py`
- `packages/db/repositories/search.py`
- `tests/unit/db/`
- `tests/unit/orchestrator/`
- `services/orchestrator/tests/`
- `migrations/versions/`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/phases/phase-4.md`

## 6. Milestones

### Milestone 1
- intent: establish Phase 4 policy and storage seams
- code changes: acquisition policy helpers, HTTP fetcher, object-store abstraction, filesystem snapshot store, settings, and any necessary migration
- validation: unit tests for policy enforcement, HTTP response handling, object storage, and migration checks

### Milestone 2
- intent: persist acquisition attempts and snapshots into the ledger
- code changes: fetch repositories plus an acquisition service that creates jobs, records attempts, writes snapshots, and keeps side effects guarded
- validation: repository and service tests

### Milestone 3
- intent: expose the minimal operator-facing Phase 4 API
- code changes: acquisition execution and ledger read routes, schemas, dependency wiring, and API tests
- validation: API tests plus a narrow manual flow against a local fake SearXNG server and a real public HTTP target

### Milestone 4
- intent: keep docs synchronized with the Phase 4 implementation
- code changes: update architecture, API, schema, runbook, and phase docs, and keep this plan current
- validation: manual doc-to-code comparison

## 7. Implementation log

- 2026-04-23 research: reread `AGENTS.md`, `deep_research_codex_dev_spec.md`, `PLANS.md`, `code_review.md`, and the current docs. Confirmed Phase 4 should stop at HTTP acquisition, attempt recording, snapshot persistence, and explicit policy documentation, with no parsing, indexing, claim, or reporting work.
- 2026-04-23 milestone 1: unified the checked-in spec filename references, made the Phase 3 candidate provenance TODO explicit, added `services/orchestrator/app/acquisition/` plus `services/orchestrator/app/storage/`, extended settings for acquisition bounds and snapshot storage, and added migration `20260423_0003` for `uq_fetch_job_candidate_url_id_mode`.
- 2026-04-23 milestone 2: extended fetch repositories and added `AcquisitionService` to create idempotent `HTTP` fetch jobs, record attempt traces, persist filesystem-backed snapshots, and clean up stored objects on database rollback.
- 2026-04-23 milestone 3: added thin acquisition routes and schemas for `POST /fetches`, `GET /fetch-jobs`, `GET /fetch-attempts`, and `GET /content-snapshots`, while keeping task status semantics and Phase 2 event behavior unchanged.
- 2026-04-23 milestone 4: updated `docs/api.md`, `docs/schema.md`, `docs/runbook.md`, `docs/architecture.md`, `docs/phases/phase-4.md`, and the related Phase 3 plan to keep operator-facing contracts aligned with the implementation.

## 8. Validation

- completed:
  - `python3 -m pytest tests/unit/orchestrator/test_acquisition_http_client.py tests/unit/orchestrator/test_snapshot_storage.py tests/unit/orchestrator/test_acquisition_service.py services/orchestrator/tests/test_acquisition_api.py tests/unit/db/test_repositories.py tests/unit/db/test_migrations.py -q`
  - `python3 -m ruff check .`
  - `python3 -m black --check .`
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit`
  - `python3 -m pytest`
  - `DATABASE_URL=sqlite:////tmp/deepresearch_phase4_manual.db python3 -m alembic -c alembic.ini upgrade head`
  - `DATABASE_URL=sqlite:////tmp/deepresearch_phase4_manual.db python3 -m alembic -c alembic.ini downgrade 20260423_0002`
  - manual API flow against a temporary SQLite database, a fake local SearXNG server on `http://127.0.0.1:18080`, and the public HTTP target `https://example.com/`, covering `GET /healthz`, `GET /readyz`, `POST /api/v1/research/tasks`, `POST /api/v1/research/tasks/{task_id}/searches`, `POST /api/v1/research/tasks/{task_id}/fetches`, `GET /api/v1/research/tasks/{task_id}/fetch-jobs`, `GET /api/v1/research/tasks/{task_id}/fetch-attempts`, and `GET /api/v1/research/tasks/{task_id}/content-snapshots`

- known unvalidated areas:
  - live MinIO interoperability because Phase 4 will start with filesystem-backed storage only
  - PostgreSQL-specific behavior because the current environment has no PostgreSQL service
  - Docker-based validation because `docker` is unavailable in the current environment

## 9. Risks and unknowns

- SSRF controls must be conservative enough to block loopback, private, and metadata targets without accidentally allowing DNS-based bypasses
- writing snapshots to storage before the database commit introduces rollback concerns that need explicit handling
- adding a fetch-job uniqueness constraint must not block later retry semantics, which should remain modeled through `fetch_attempt`

## 10. Rollback / recovery

- revert the Phase 4 acquisition files, repositories, routes, schemas, tests, and docs
- if a migration is added, document the exact downgrade command and any data-shape caveats before finishing the turn

## 11. Deferred work

- browser fallback and Playwright
- attachment discovery and Tika
- OpenSearch indexing
- worker scheduling and retry orchestration
- MinIO-backed object storage
