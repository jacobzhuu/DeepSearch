# Phase 10 Real Infrastructure And Observability

## 1. Objective

Harden the current Phase 9 research kernel into a host-local / self-hosted real-infrastructure integration slice by adding a MinIO-backed object-store implementation, stronger OpenSearch validation and error handling, minimal JSON logging plus metrics, and `report_artifact` provenance hardening with a content hash and manifest snapshot. Validate the existing ORM, repositories, migrations, and API slices against real PostgreSQL, OpenSearch, and MinIO processes on the primary host-local path. Docker or compose may remain documented as optional tooling, but they are not the primary Phase 10 acceptance gate.

## 2. Why this exists

Phase 9 closed the functional v1 loop, but it still behaves like a local-dev shell in several operationally important areas: object storage is filesystem-only, OpenSearch startup validation is configuration-only, report artifacts do not persist their own hash or provenance snapshot, and there is no minimal metrics surface for operators. Phase 10 needs to make the existing slices actually operable and auditable against real infrastructure on a self-hosted Linux path without widening product semantics or starting future-phase workflow behavior.

## 3. Scope

### In scope

- add a Phase 10 `report_artifact` hardening migration for:
  - `content_hash`
  - minimal `manifest_json`
- update ORM, repositories, and report synthesis to persist those fields
- add a MinIO object-store backend that coexists with filesystem storage
- fail app startup on invalid MinIO or OpenSearch configuration
- strengthen OpenSearch backend behavior for:
  - live startup validation
  - index creation
  - write/retrieve error wrapping
- add minimal JSON logs
- add minimal Prometheus-style metrics and a thin `/metrics` endpoint
- increment key task, fetch, parse, verify, and report counters from existing service flows
- add targeted unit tests plus narrow integration coverage for the new seams
- update docs and this plan
- run real PostgreSQL validation
- run real MinIO and OpenSearch validation when the host environment allows it
- keep Docker / compose optional rather than required

### Out of scope

- OpenClaw integration
- HTML or PDF export
- new planner or gap-analyzer behavior
- new claim drafting or verifier semantics
- new search, fetch, parse, or retrieval product capabilities
- browser automation, Tika, or worker orchestration
- broad API redesign

## 4. Constraints

- stay strictly within Phase 10
- preserve current Phase 2 through Phase 9 API semantics
- do not change the main user-facing report body format away from Markdown
- keep provenance explicit and reversible
- keep existing filesystem object-store support
- pin any new production dependency
- if a real dependency cannot be started on this host, say so explicitly and keep the code/documentation in a host-local operable state

## 5. Relevant files and systems

- `pyproject.toml`
- `docker-compose.dev.yml`
- `.env.example`
- `migrations/versions/`
- `packages/db/models/ledger.py`
- `packages/db/repositories/reports.py`
- `packages/db/session.py`
- `packages/observability/`
- `services/orchestrator/app/main.py`
- `services/orchestrator/app/settings.py`
- `services/orchestrator/app/storage/`
- `services/orchestrator/app/indexing/`
- `services/orchestrator/app/services/`
- `services/orchestrator/app/api/routes/health.py`
- `tests/unit/db/`
- `tests/unit/orchestrator/`
- `services/orchestrator/tests/`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/architecture.md`
- `docs/phases/phase-10.md`

## 6. Milestones

### Milestone 1
- intent: harden `report_artifact` provenance and idempotency metadata
- code changes:
  - migration
  - ORM and repository updates
  - report synthesis manifest plus content-hash persistence
  - report tests
- validation:
  - migration tests
  - report repository/service/API tests

### Milestone 2
- intent: add production-style storage and search backend hardening
- code changes:
  - MinIO object-store backend
  - OpenSearch live validation and backend error mapping
  - settings and startup validation
  - backend-focused tests
- validation:
  - storage and indexing unit tests
  - startup validation tests

### Milestone 3
- intent: add minimum operability surfaces
- code changes:
  - JSON logging
  - `/metrics`
  - task/fetch/parse/verify/report counters
  - observability tests
- validation:
  - unit tests
  - API-level metrics check

### Milestone 4
- intent: validate against real infrastructure where the host allows it
- code changes:
  - compose and runbook updates
  - optional narrow integration helpers if needed
- validation:
  - `alembic upgrade/downgrade` on real PostgreSQL
  - narrow ORM/repository/API path on real PostgreSQL
  - narrow MinIO write/read/delete path
  - narrow OpenSearch index/write/retrieve path

## 7. Implementation log

- 2026-04-24 research:
  - reread `deep_research_codex_dev_spec.md`, `PLANS.md`, `code_review.md`, and current docs
  - confirmed Phase 10 should focus on deploy hardening and real dependency validation, not new product behavior
  - confirmed current host lacks `docker`, `postgres`, and `minio`, but does provide root access, so real-process validation is still potentially possible
- 2026-04-24 implementation:
  - added `report_artifact.content_hash` and `report_artifact.manifest_json` through Alembic, ORM, repository, and report synthesis updates
  - introduced a MinIO-backed object-store implementation with startup-time bucket validation while preserving the filesystem backend
  - added JSON logging, `/metrics`, and additive counters for task, fetch, parse, verify, and report flows
  - tightened startup validation to cover object-store configuration and optional live OpenSearch connectivity
  - discovered against a real OpenSearch 2.19 node that `httpx` requests timed out unless `Accept-Encoding: identity` was forced, so the backend now always sends that header
  - discovered against the same node that the original strict mapping rejected arbitrary `metadata` keys, so the index mapping now keeps `metadata` as a dynamic object
  - updated `docs/api.md`, `docs/schema.md`, `docs/runbook.md`, `docs/architecture.md`, and added `docs/phases/phase-10.md`
- 2026-04-24 validation:
  - validated Alembic upgrade/downgrade/upgrade on a real PostgreSQL 16.13 instance started from a micromamba prefix at `/tmp/pg-env`
  - validated a narrow PostgreSQL ORM/repository/API path by creating a task through FastAPI and confirming the persisted repository state
  - validated a real MinIO server on `127.0.0.1:9000`, bucket creation, startup validation, and object write/read/delete through the new object-store seam
  - validated a real OpenSearch 2.19.0 node on `127.0.0.1:9200`, index creation, chunk upsert, retrieval, startup connectivity validation, and a narrow PostgreSQL-backed `/index` plus `/retrieve` API path
  - re-ran full lint, format, type, and pytest checks after the real-node compatibility fixes
- 2026-04-24 route change:
  - repository goal shifted away from “someone else can directly reproduce deployment from the repo”
  - Phase 10 is now explicitly treated as host-local / self-hosted hardening and validation work
  - Docker and compose remain optional packaging only and are no longer the primary acceptance standard for this plan

## 8. Validation

- `python3 -m ruff check .` — passed
- `python3 -m black --check .` — passed
- `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit` — passed
- `python3 -m pytest` — passed, `98 passed`
- `python3 -m pytest tests/unit/orchestrator/test_indexing_backend.py services/orchestrator/tests/test_indexing_api.py services/orchestrator/tests/test_claims_api.py -q` — passed after the real OpenSearch compatibility fixes
- `DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/deepresearch_phase10 python3 -m alembic -c alembic.ini upgrade head` — passed on real PostgreSQL 16.13
- `DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/deepresearch_phase10 python3 -m alembic -c alembic.ini downgrade base` — passed on real PostgreSQL 16.13
- repeat `upgrade head` on the same PostgreSQL URL — passed
- narrow PostgreSQL ORM/repository/API script — passed
  - created one task through FastAPI against the PostgreSQL URL
  - confirmed repository readback of task status, revision number, and event count
- real MinIO validation script against `127.0.0.1:9000` — passed
  - created `snapshots` and `reports` buckets
  - validated startup configuration
  - validated object write, read, and delete through `build_snapshot_object_store(..., backend=\"minio\")`
- real OpenSearch validation script against `127.0.0.1:9200` — passed
  - live startup connectivity validation
  - index creation
  - chunk upsert
  - task-scoped retrieval
- bad OpenSearch connectivity probe on `127.0.0.1:9201` with `OPENSEARCH_VALIDATE_CONNECTIVITY_ON_STARTUP=true` — failed as expected with `IndexBackendOperationError`
- narrow PostgreSQL + OpenSearch API script — passed
  - inserted one `source_document` and one `source_chunk` in PostgreSQL
  - `POST /api/v1/research/tasks/{task_id}/index` returned `200` with `indexed_count = 1`
  - `GET /api/v1/research/tasks/{task_id}/retrieve` returned `200` with `total = 1`
- combined startup validation with `SNAPSHOT_STORAGE_BACKEND=minio` and `OPENSEARCH_VALIDATE_CONNECTIVITY_ON_STARTUP=true` — passed
- `/metrics` smoke check after a task-creation request — passed and emitted `deepresearch_http_requests_*` samples

## 9. Risks and unknowns

- real OpenSearch startup may be constrained by host memory, `vm.max_map_count`, or unavailable container tooling
- introducing live startup validation must not make test setup brittle
- `report_artifact` manifest shape should stay minimal to avoid pretending Phase 10 has a full report-history subsystem
- metrics must remain additive and not leak future-phase semantics
- the dev compose stack still does not start PostgreSQL, MinIO, or OpenSearch; Phase 10 validation here used host-local ad hoc processes instead
- the current OpenSearch fix assumes `Accept-Encoding: identity` is the safest cross-version choice; if a later deployment needs compressed responses, that path will need explicit revalidation
- if the project later returns to a wider reproducible-deployment target, compose and packaging validation will need separate follow-up work

## 10. Rollback / recovery

- revert the Phase 10 migration, object-store, indexing, observability, and doc changes together
- downgrade the new Alembic revision if one is added
- remove any locally started PostgreSQL, MinIO, or OpenSearch data directories used for validation

## 11. Deferred work

- worker and queue orchestration
- OpenClaw
- HTML/PDF export
- richer report provenance beyond the minimal manifest snapshot
- full distributed tracing
- advanced alerting and dashboards
