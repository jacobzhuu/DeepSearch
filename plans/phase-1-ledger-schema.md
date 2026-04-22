# Phase 1 Initial Ledger Schema, ORM, And Repository Scaffold

## 1. Objective

Introduce the first database layer for the Deep Research platform by initializing Alembic, creating the initial ledger schema migration for the required core entities, adding aligned SQLAlchemy 2.x ORM models, and adding a minimal repository layer plus validation tests.

## 2. Why this exists

Phase 1 establishes the persistent ledger foundation that later phases depend on. The system cannot safely implement task lifecycle, evidence tracking, or report traceability without a reversible schema, explicit constraints, and a consistent persistence layer.

## 3. Scope

### In scope

- initialize Alembic at the repository root
- add the first migration for:
  - `research_task`
  - `research_run`
  - `task_event`
  - `search_query`
  - `candidate_url`
  - `fetch_job`
  - `fetch_attempt`
  - `content_snapshot`
  - `source_document`
  - `source_chunk`
  - `citation_span`
  - `claim`
  - `claim_evidence`
  - `report_artifact`
- add SQLAlchemy 2.x ORM models that match the migration
- add a minimal repository layer grouped by ledger concern
- add migration and repository tests
- update schema and operator docs for Phase 1

### Out of scope

- task orchestration, scheduling, leases, or state-machine execution logic
- search, crawl, parse, index, verification, or reporting behavior
- public research task APIs beyond the existing health endpoints
- background workers, queues, or external service integrations
- production database operations and data backfills

## 4. Constraints

- stay strictly within Phase 1
- write migration before ORM and repository code
- keep the schema reversible
- prefer explicit indexes, unique constraints, and check constraints where the spec is concrete
- avoid inventing future-phase workflow or provider behavior that is not required for Phase 1
- keep repository methods thin and persistence-focused

## 5. Relevant files and systems

- `pyproject.toml`
- `.pre-commit-config.yaml`
- `.env.example`
- `Makefile`
- `alembic.ini`
- `migrations/env.py`
- `migrations/versions/`
- `packages/db/`
- `tests/unit/db/`
- `docs/architecture.md`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/phases/phase-1.md`

## 6. Milestones

### Milestone 1
- intent: initialize Alembic and encode the initial ledger schema in a reversible migration
- code changes: add Alembic config, migration environment, and a first revision with tables, constraints, and indexes
- validation: run `alembic upgrade head` and `alembic downgrade base` against a temporary SQLite database

### Milestone 2
- intent: add ORM models and session helpers that exactly match the migrated schema
- code changes: add shared metadata, model definitions, and database session helpers
- validation: import metadata successfully and persist sample rows through SQLAlchemy sessions

### Milestone 3
- intent: add thin repositories and cover the risky schema paths with tests
- code changes: add repository modules and unit tests for migration application, round-trip persistence, and key uniqueness guarantees
- validation: run `pytest`, `mypy`, `ruff`, and `black --check`

### Milestone 4
- intent: document the new persistence layer and operator commands
- code changes: update architecture, schema, API, and runbook docs; add a Phase 1 doc
- validation: manually compare commands and table descriptions against the implemented files

## 7. Implementation log

- 2026-04-22 session start: reread `AGENTS.md`, `deep_research_codex_dev_spec.md`, `PLANS.md`, `code_review.md`, and the current docs. Confirmed Phase 1 should stop at schema, ORM, repository, and tests, with no task API or workflow implementation. Plan created.
- 2026-04-22 migration: added `alembic.ini`, the Alembic environment, and the initial revision `20260422_0001` covering the requested ledger entities with explicit foreign keys, unique constraints, indexes, and reversible downgrade behavior.
- 2026-04-22 persistence layer: added `packages/db` with shared metadata, SQLAlchemy 2.x models, SQLite-aware engine/session helpers, and thin repositories grouped by research, search, fetch, source, claim, and report concerns.
- 2026-04-22 validation and docs: added migration and repository tests, updated the schema and runbook docs to Phase 1, and validated the migration commands, lint, formatting, typing, and pytest paths successfully.

## 8. Validation

- `python3 -m pip install -e ".[dev]"` - passed
- `DATABASE_URL=sqlite:////tmp/deepresearch_phase1.db python3 -m alembic -c alembic.ini upgrade head` - passed
- `DATABASE_URL=sqlite:////tmp/deepresearch_phase1.db python3 -m alembic -c alembic.ini downgrade base` - passed
- `python3 -m ruff check .` - passed
- `python3 -m black --check .` - passed
- `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit` - passed
- `python3 -m pytest` - passed, `6 passed`

- known unvalidated areas:
  - PostgreSQL-specific runtime behavior because the current environment has no PostgreSQL service
  - Docker-based migration workflow because `docker` is unavailable in the current environment

## 9. Risks and unknowns

- SQLite is suitable for migration and repository tests here, but the primary production target remains PostgreSQL 16
- several fields such as fetch mode, fetch status, source type, and claim type are intentionally left as constrained text only where the spec is explicit, to avoid leaking future workflow semantics into Phase 1
- the repository layer will remain thin; richer transaction orchestration is deferred

## 10. Rollback / recovery

- downgrade the initial revision back to `base`
- remove the new Phase 1 files if a full file-level rollback is needed before the migration is adopted anywhere persistent

## 11. Deferred work

- `research_plan`, `attachment`, and `domain_policy` tables
- task lifecycle APIs and event-stream endpoints
- worker protocol, leases, retries, and resume checkpoints beyond schema storage
- search, fetch, parse, index, verify, and report runtime implementations
