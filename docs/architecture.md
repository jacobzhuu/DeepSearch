# Architecture

## Current phase

This repository is in Phase 2. The service now exposes a thin research task API backed by the Phase 1 persistence layer. Task creation, lookup, event retrieval, and pause, resume, cancel, and revise transitions exist as database-only state changes. No worker, queue, search, crawling, parsing, indexing, verification, or reporting runtime logic exists yet.

## Layer boundaries

- UI / gateway layer: not implemented yet
- orchestrator / workflow layer: `services/orchestrator/app/` now contains the thin research task API, request and response schemas, database dependencies, and the task service layer
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
- keep migration, ORM, repository, and service behavior aligned
- keep repository and service code free of scheduling, search, fetch, and claim-generation behavior
- keep health and readiness endpoints free of external dependency checks until backing services are introduced
