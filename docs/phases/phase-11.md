# Phase 11

## Goal

Close out the current v1 candidate around the primary host-local / self-hosted Linux path: make the existing real-dependency workflow easier to operate through explicit initialization scripts, operator runbook updates, and a minimum end-to-end smoke path, while keeping Docker / compose as optional deployment packaging only.

## Deliverables

- base `docker-compose.yml` for:
  - PostgreSQL
  - MinIO
  - OpenSearch
  - orchestrator
- dev override `docker-compose.dev.yml` that keeps the same topology but simplifies local OpenSearch wiring
- optional compose services for:
  - SearXNG
  - Tika
- deployment helpers:
  - `scripts/migrate.sh`
  - `scripts/init_buckets.py`
  - `scripts/init_index.py`
  - `scripts/smoke_test.py`
  - `scripts/mock_searxng.py` for deterministic local smoke without Docker
- Docker image packaging that now includes `scripts/`
- host-local runbook and smoke flow as the primary operator path
- runbook coverage for:
  - environment variables
  - startup order
  - health checks
  - shutdown
  - troubleshooting
- updated architecture, API, schema, and ExecPlan documentation

## Explicitly excluded

- OpenClaw integration
- HTML or PDF export
- new planner or gap-analyzer behavior
- new verifier semantics
- new search, fetch, parse, or retrieval capabilities
- new public API semantics
- dashboarding, tracing, or broader platform automation beyond the existing Phase 10 observability baseline
- treating “someone else can take the repo and directly reproduce deployment” as the main success criterion
