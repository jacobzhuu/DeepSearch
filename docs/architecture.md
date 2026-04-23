# Architecture

## Current phase

This repository is in Phase 2. The service now exposes a thin research task API backed by the Phase 1 persistence layer. Task creation, lookup, event retrieval, and pause, resume, cancel, and revise transitions exist as database-only state changes. The ledger now also carries per-task `revision_no` and ordered `task_event.sequence_no` values so the next phase can attach runs and snapshots without ambiguous task history. No worker, queue, search, crawling, parsing, indexing, verification, or reporting runtime logic exists yet.

## Layer boundaries

- UI / gateway layer: not implemented yet
- orchestrator / workflow layer: `services/orchestrator/app/` now contains the thin research task API, request and response schemas, database dependencies, the task service layer, and the current Phase 2 runtime-readiness contract
- persistence / ledger layer: `migrations/` and `packages/db/` hold the schema, ORM, session helpers, and repositories
- acquisition / parsing / indexing layer: placeholder directories only, with ledger tables ready for later phases
- reporting / delivery layer: placeholder directories only

## Repository shape

- `services/orchestrator/`: runnable FastAPI service skeleton for future research task APIs
- `services/orchestrator/app/services/`: thin task state transition logic for Phase 2
- `packages/db/`: SQLAlchemy models, session helpers, and repository skeletons for the research ledger
- `migrations/`: Alembic environment and the initial reversible schema migration
- `services/crawler/`, `services/reporter/`, `services/openclaw/`: directory placeholders only
- `packages/`: reserved for shared packages introduced in later phases
- `infra/`: reserved for infrastructure configuration introduced incrementally
- `docs/phases/phase-0.md`: current phase scope and deliverables
- `docs/phases/phase-1.md`: current schema-phase scope and deliverables
- `docs/phases/phase-2.md`: task API and event-stream scope and deliverables

## Phase 2 design constraints

- keep the product centered on `research_task`, but do not start background execution yet
- keep the task API limited to creation, lookup, event retrieval, and database-only pause, resume, cancel, and revise transitions
- keep `resume` limited to returning a task to the current executable-candidate status; it must not imply queueing or execution yet
- keep future runtime-facing statuses reserved in code and schema without making them user-writable in the current API
- keep migration, ORM, repository, and service behavior aligned
- keep repository and service code free of scheduling, search, fetch, and claim-generation behavior
- keep health and readiness endpoints free of external dependency checks until backing services are introduced
