# Phase 0 Repository Scaffold And Engineering Discipline

## 1. Objective

Build the Phase 0 repository scaffold for the Deep Research platform: directory layout, pinned Python tooling, a minimal FastAPI orchestrator service, local developer entrypoints, and the smallest documentation needed to run and validate the scaffold.

## 2. Why this exists

Phase 0 establishes a runnable and reviewable engineering baseline before any database, workflow, search, or evidence logic is introduced. The goal is to make later phases auditable, testable, and reversible instead of mixing infrastructure setup with business logic.

## 3. Scope

### In scope

- create the repository directory skeleton for the documented architecture
- add a pinned root `pyproject.toml` for the current Python toolchain
- configure `ruff`, `black`, `pytest`, `mypy`, and `pre-commit`
- add a minimal FastAPI orchestrator service with `/healthz` and `/readyz`
- add `docker-compose.dev.yml`, `.env.example`, and a root `Makefile`
- add Phase 0 docs and tests for the health endpoints

### Out of scope

- database connections, migrations, ORM models, or repositories
- `research_task` API endpoints beyond health and readiness
- workers, queues, retries, state machines, checkpointing, or ledger entities
- search, crawl, parse, index, verify, reporting, or object storage logic
- production deployment hardening

## 4. Constraints

- implement only Phase 0
- do not introduce database or search business logic
- keep all dependency and image versions pinned
- keep architecture boundaries visible in the directory layout
- document operator-facing behavior introduced in this phase

## 5. Relevant files and systems

- `pyproject.toml`
- `Makefile`
- `.pre-commit-config.yaml`
- `.env.example`
- `docker-compose.dev.yml`
- `services/orchestrator/Dockerfile`
- `services/orchestrator/app/`
- `services/orchestrator/tests/`
- `docs/architecture.md`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/phases/phase-0.md`

## 6. Milestones

### Milestone 1
- intent: establish the Phase 0 repository layout and execution plan
- code changes: add directory skeleton, placeholder tracking files, and this ExecPlan
- validation: inspect tree and confirm Phase 0 paths exist

### Milestone 2
- intent: make the orchestrator service runnable with pinned tooling
- code changes: add `pyproject.toml`, service package, Dockerfile, compose file, env example, and Makefile
- validation: run lint, type, test, and start the FastAPI app locally

### Milestone 3
- intent: document the scaffold and its current phase boundary
- code changes: add API, architecture, schema, runbook, and phase docs
- validation: verify docs match the implemented commands and endpoints

## 7. Implementation log

- 2026-04-22 session start: reviewed `deep_research_codex_dev_spec.md`, `PLANS.md`, `code_review.md`, and `AGENTS.md`; confirmed `docs/architecture.md`, `docs/api.md`, `docs/schema.md`, and `docs/runbook.md` were missing; constrained work to Phase 0 only. Plan created.
- 2026-04-22 implementation: created the repository skeleton, pinned Python toolchain, minimal FastAPI app, Docker compose file, Make targets, pre-commit config, and health endpoint tests. Documentation added for the current scaffold boundary.
- 2026-04-22 validation: `python3 -m pip install -e ".[dev]"` passed on host Python 3.12 after widening local metadata support to `>=3.11,<3.13`; container runtime remains pinned to `python:3.11.11-slim-bookworm`. `make lint` could not be executed because `make` is not installed in the environment, so the underlying commands were run directly instead. `ruff`, `black --check`, `mypy`, `pytest`, and manual `uvicorn` plus `curl` checks all passed.

## 8. Validation

- `python3 -m pip install -e ".[dev]"` - passed
- `make lint` - could not run because `make` is not installed in the current environment
- `python3 -m ruff check .` - passed
- `python3 -m black --check .` - passed
- `python3 -m mypy services/orchestrator/app services/orchestrator/tests` - passed
- `python3 -m pytest` - passed
- `python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000` - started successfully
- `curl -fsS http://127.0.0.1:8000/healthz` - passed
- `curl -fsS http://127.0.0.1:8000/readyz` - passed

- known unvalidated areas:
  - Docker image build path was not run because `docker` is not installed in the current environment
  - `pre-commit install` was configured but not executed because hook installation is lower priority than source validation for Phase 0

## 9. Risks and unknowns

- using a single root `pyproject.toml` is the thinnest Phase 0 setup, but later phases may choose to split service-specific packaging if dependencies diverge
- `docker-compose.dev.yml` assumes a local `.env` file copied from `.env.example`
- readiness is intentionally static in Phase 0 because no downstream dependencies exist yet
- the host only provided Python 3.12, so local metadata was widened to `>=3.11,<3.13`; the pinned container runtime remains the primary 3.11.11 target

## 10. Rollback / recovery

- remove the Phase 0 scaffold files if the repository needs to return to a documentation-only state
- because no schema or persisted runtime state is introduced, rollback is file-only

## 11. Deferred work

- Phase 1 Alembic setup and database schema
- task APIs and event stream endpoints
- worker, queue, and checkpoint/resume semantics
- search, fetch, parse, index, and report services
