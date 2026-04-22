# Architecture

## Current phase

This repository is in Phase 0. Only the minimal orchestrator service scaffold is implemented. No database, queue, search, crawling, parsing, indexing, verification, or reporting logic exists yet.

## Layer boundaries

- UI / gateway layer: not implemented yet
- orchestrator / workflow layer: `services/orchestrator/app/` contains only the FastAPI service shell and system endpoints
- acquisition / parsing / indexing layer: placeholder directories only
- reporting / delivery layer: placeholder directories only

## Repository shape

- `services/orchestrator/`: runnable FastAPI service skeleton for future research task APIs
- `services/crawler/`, `services/reporter/`, `services/openclaw/`: directory placeholders only
- `packages/`: reserved for shared packages introduced in later phases
- `infra/`: reserved for infrastructure configuration introduced incrementally
- `docs/phases/phase-0.md`: current phase scope and deliverables

## Phase 0 design constraints

- keep the product centered on `research_task` in future phases, but do not implement task logic yet
- keep the directory structure aligned with the long-term architecture
- keep health and readiness endpoints free of external dependency checks until those dependencies exist
