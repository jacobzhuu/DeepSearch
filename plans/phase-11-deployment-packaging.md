# Phase 11 Deployment Packaging And Smoke Validation

## 1. Objective

Close out the current Phase 10 v1 candidate around the primary host-local / self-hosted Linux path by adding and tightening deployment helpers, initialization scripts, operator documentation, and a minimum end-to-end smoke test that exercises the existing task, search, fetch, parse, index, draft, verify, and report flow without changing product API semantics. Docker and compose may remain as optional deployment packaging, but they are not the main success criterion.

## 2. Why this exists

Phase 10 proved the individual runtime seams against real PostgreSQL, MinIO, and OpenSearch processes, but the repository still lacked a clearer operator path for starting those dependencies, bootstrapping them, and verifying the whole chain. Phase 11 closes that gap primarily for the repository owner on a self-hosted Linux path. Compose artifacts may stay in the repo, but they are no longer the principal delivery target.

## 3. Scope

### In scope

- add a base `docker-compose.yml` for:
  - PostgreSQL
  - MinIO
  - OpenSearch
  - orchestrator
- keep optional compose services for:
  - SearXNG
  - Tika
- add a dev override with simpler local defaults
- add the minimum deployment config seam required for OpenSearch security-aware production wiring without changing API semantics
- add bootstrap scripts for:
  - database migration
  - bucket initialization
  - index initialization
  - smoke test
- add a minimum smoke path covering:
  - task
  - search
  - fetch
  - parse
  - index
  - draft
  - verify
  - report
- update runbook and phase docs
- keep host-local / self-hosted Linux as the primary documented operator path

### Out of scope

- OpenClaw integration
- HTML or PDF export
- planner or gap-analyzer behavior
- new verifier semantics
- new search, fetch, parse, or retrieval capabilities
- dashboards, tracing, or broader platform automation
- multi-node clustering beyond the current single-node self-hosted target
- treating Docker or compose as the required acceptance gate

## 4. Constraints

- stay strictly within Phase 11
- do not change main API semantics
- keep the deployment package reversible and explicit
- preserve the existing filesystem backend and Phase 10 runtime paths
- distinguish dev simplifications from prod-like security boundaries, especially for OpenSearch
- do not silently widen app runtime behavior to future phases
- keep host-local / self-hosted Linux as the primary closeout path
- do not treat “someone else can directly reproduce deployment” as the current objective

## 5. Relevant files and systems

- `docker-compose.yml`
- `docker-compose.dev.yml`
- `.env.example`
- `.env.compose.example`
- `Makefile`
- `services/orchestrator/Dockerfile`
- `services/orchestrator/app/settings.py`
- `services/orchestrator/app/indexing/backends.py`
- `services/orchestrator/app/main.py`
- `scripts/`
- `infra/opensearch/`
- `infra/minio/`
- `infra/searxng/`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/architecture.md`
- `docs/phases/phase-11.md`

## 6. Milestones

### Milestone 1
- intent: add the minimum runtime config seam needed for dev and prod separation
- code changes:
  - OpenSearch auth and TLS env/config support
  - indexing backend updates and tests
  - `.env.example` updates
- validation:
  - targeted backend tests
  - full lint, type, and pytest after the change

### Milestone 2
- intent: add optional deployment packaging and explicit initialization artifacts
- code changes:
  - base compose
  - dev override
  - optional SearXNG and Tika profiles
  - migration, bucket-init, and index-init scripts
  - container image copy path updates for scripts
- validation:
  - config parsing checks
  - narrow script validation against host-local services where available

### Milestone 3
- intent: add the end-to-end smoke path and operator documentation
- code changes:
  - smoke test script
  - runbook and phase docs
  - optional Make targets for deployment flows
- validation:
  - smoke flow against the narrowest realistic local stack
  - explicit documentation of any unvalidated compose behavior

## 7. Implementation log

- 2026-04-24 research:
  - reread `deep_research_codex_dev_spec.md`, `PLANS.md`, `code_review.md`, and current docs before touching Phase 11
  - confirmed the existing repository only has a Phase 0-style `docker-compose.dev.yml` and no clear host-local closeout runbook yet
  - confirmed current host still lacks Docker, so Phase 11 compose validation must rely on config inspection plus host-local process checks where possible
- 2026-04-24 milestone 1:
  - added OpenSearch auth and TLS settings to the app config surface
  - updated the OpenSearch backend builder and runtime wiring to carry username, password, TLS verify mode, and CA bundle path
  - kept the backend constructor backward compatible for existing tests by providing safe defaults
  - next: add compose packaging and initialization scripts
- 2026-04-24 milestone 2:
  - added `docker-compose.yml` as the prod-like single-node stack and repurposed `docker-compose.dev.yml` as a dev override
  - added `.env.compose.example`
  - updated the orchestrator Dockerfile to copy `scripts/`
  - added `scripts/migrate.sh`, `scripts/init_buckets.py`, `scripts/init_index.py`, `scripts/mock_searxng.py`, and `scripts/smoke_test.py`
  - added Make targets for deploy and smoke helpers
  - next: update operator docs and run host-local validation
- 2026-04-24 milestone 3:
  - rewrote `docs/runbook.md` around Phase 11 deployment order, env vars, health checks, and troubleshooting
  - updated architecture, API, schema, and phase docs to reflect “no new API semantics, deployment packaging only”
  - host-local real-process validation exposed two deployment-time defects:
    - `scripts/smoke_test.py` was inheriting proxy env vars; fixed by setting `trust_env=False`
    - `fetch` and `parse` batch logs used reserved `LogRecord` field names like `created`; fixed by renaming them to non-reserved keys
  - host-local smoke now passes end to end against real PostgreSQL, real MinIO, real OpenSearch, and the deterministic mock SearXNG helper
- 2026-04-24 route change:
  - repository goal shifted to a single-operator, host-local / self-hosted Linux path
  - compose artifacts remain optional tooling, not the primary definition of success for this plan
  - future closeout work should prefer docs, scripts, and operator recovery clarity over wider packaging scope

## 8. Validation

- completed:
  - `python3 -m py_compile scripts/init_buckets.py scripts/init_index.py scripts/mock_searxng.py scripts/smoke_test.py` — passed
  - `python3 -m pytest tests/unit/orchestrator/test_indexing_backend.py -q` — passed after making the backend constructor backward compatible
  - `sh ./scripts/migrate.sh history` — passed
  - `python3 -m ruff check .` — passed
  - `python3 -m black --check .` — passed
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit` — passed
  - `python3 -m pytest` — passed
  - `python3 -m alembic -c alembic.ini upgrade head` on PostgreSQL 16.13 — passed
  - `python3 scripts/init_buckets.py` against real MinIO — passed and created `snapshots` plus `reports`
  - `python3 scripts/init_index.py` against real OpenSearch 2.19.0 — passed
  - `./scripts/migrate.sh upgrade head` and `./scripts/migrate.sh current` against PostgreSQL 16.13 — passed
  - `python3 scripts/smoke_test.py --base-url http://127.0.0.1:8000` — passed against real PostgreSQL, real MinIO, real OpenSearch, and `scripts/mock_searxng.py`
  - `curl -fsS http://127.0.0.1:8000/healthz` — passed
  - `curl -fsS http://127.0.0.1:8000/readyz` — passed
  - `curl -fsS http://127.0.0.1:8000/metrics | rg 'deepresearch_http_requests_total|deepresearch_report_results_total' -m 4` — passed
- known host limitation:
  - `docker` is still unavailable on this machine, so `docker compose config/up` remains unvalidated in this turn
  - this is now acceptable because compose is optional tooling rather than the primary acceptance path

## 9. Risks and unknowns

- prod-like OpenSearch security wiring may require a minimal auth/TLS seam in app config even though Phase 11 must not change API semantics
- smoke determinism depends on search-provider behavior unless a controlled search endpoint is used
- compose syntax can be written correctly yet remain unvalidated without a local compose binary
- the prod-like compose path assumes the operator supplies `infra/opensearch/certs/root-ca.pem`; this file is intentionally not generated in Phase 11
- if the project later returns to a broader reproducible-deployment target, compose runtime validation will need a dedicated follow-up milestone

## 10. Rollback / recovery

- revert compose, script, deployment-config, and doc changes together
- if OpenSearch config seams are added, revert them together with compose changes to avoid leaving dead env vars behind
- remove any temporary data directories or local helper processes used for validation

## 11. Deferred work

- full production certificate management
- multi-node OpenSearch or PostgreSQL topologies
- dashboards and tracing
- OpenClaw
- HTML/PDF export
- optional Docker Compose runtime validation once a host with `docker` is available
