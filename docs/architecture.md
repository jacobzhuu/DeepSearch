# Architecture

## Current phase

This repository is in Phase 1. The minimal orchestrator service scaffold from Phase 0 remains in place, and the initial persistence layer now exists through Alembic migrations, SQLAlchemy models, and thin repositories. No task workflow, queue, search, crawling, parsing, indexing, verification, or reporting runtime logic exists yet.

## Layer boundaries

- UI / gateway layer: not implemented yet
- orchestrator / workflow layer: `services/orchestrator/app/` contains only the FastAPI service shell and system endpoints
- persistence / ledger layer: `migrations/` and `packages/db/` hold the schema, ORM, session helpers, and repositories
- acquisition / parsing / indexing layer: placeholder directories only, with ledger tables ready for later phases
- reporting / delivery layer: placeholder directories only

## Repository shape

- `services/orchestrator/`: runnable FastAPI service skeleton for future research task APIs
- `packages/db/`: SQLAlchemy models, session helpers, and repository skeletons for the research ledger
- `migrations/`: Alembic environment and the initial reversible schema migration
- `services/crawler/`, `services/reporter/`, `services/openclaw/`: directory placeholders only
- `packages/`: reserved for shared packages introduced in later phases
- `infra/`: reserved for infrastructure configuration introduced incrementally
- `docs/phases/phase-0.md`: current phase scope and deliverables
- `docs/phases/phase-1.md`: current schema-phase scope and deliverables

## Phase 1 design constraints

- keep the product centered on `research_task`, but do not implement task APIs or workflow execution yet
- keep migration, ORM, and repository definitions aligned
- keep repository code persistence-focused and free of scheduling, search, or claim-generation behavior
- keep health and readiness endpoints free of external dependency checks until backing services are introduced
