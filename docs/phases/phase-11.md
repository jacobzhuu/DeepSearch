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
  - `scripts/research_worker.py`
  - `scripts/mock_searxng.py` for deterministic local smoke without Docker
- Docker image packaging that now includes `scripts/`
- host-local runbook and smoke flow as the primary operator path
- managed `dev.sh` host-local helper for repeatable backend/frontend restart, optional mock
  search startup, initialization, status, logs, doctor checks, and smoke execution
- runbook coverage for:
  - environment variables
  - startup order
  - health checks
  - shutdown
  - troubleshooting
- post-MVP hardening for the completed host-local loop:
  - SearXNG endpoint validation and structured diagnostics
  - visible smoke/local/no-LLM mode separation in the web workspace
  - pre-run research plan generation, JSON edit, and confirmation before pipeline execution
  - task-event/task-detail visibility for search results, selected sources, fetch failures, and low-source warnings
  - deterministic filtering of weak claim and citation material before new claims or regenerated reports
  - `zh-CN` report language default in the web workspace with backend `report_language` / `constraints.language` support
  - optional grounded LLM report writer constrained to verified claim/evidence/citation-span ids
  - product `/run` queueing with a host-local worker, task-event progress polling, and `research_run.checkpoint_json` stage checkpoints
  - deterministic post-verification gap analysis that can append bounded supplemental search/fetch/parse/index/draft/verify rounds before reporting, including per-slot multi-round query variants and fallback attempts against existing unattempted high-value candidates when supplemental search returns only duplicate URLs
  - report page Raw Markdown, Copy Markdown, and Download `.md` controls
- updated architecture, API, schema, and ExecPlan documentation

## Explicitly excluded

- OpenClaw integration
- HTML or PDF export
- LLM-authored planner or gap-analyzer behavior
- distributed worker leases or external queue infrastructure
- new verifier semantics
- new search, fetch, parse, or retrieval capabilities
- broad public API expansion beyond queueing the existing run endpoint
- dashboarding, tracing, or broader platform automation beyond the existing Phase 10 observability baseline
- treating “someone else can take the repo and directly reproduce deployment” as the main success criterion
