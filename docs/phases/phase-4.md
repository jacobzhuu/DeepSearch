# Phase 4

## Goal

Introduce the first acquisition slice of the orchestrator: turn existing `candidate_url` rows into traceable fetch ledgers by creating `fetch_job` and `fetch_attempt`, executing a policy-guarded HTTP fetch, and persisting raw response bytes through a snapshot object-store seam into `content_snapshot`.

## Deliverables

- explicit HTTP acquisition policy with SSRF-minded target restrictions
- minimal `HTTP` acquisition client with bounded timeout, redirect, and body-size behavior
- object-store abstraction plus a filesystem-backed snapshot implementation
- minimal `fetch_job(candidate_url_id, mode)` idempotency boundary
- thin repository, service, and API paths for running acquisition and reading fetch ledgers
- tests for acquisition policy, object storage, repositories, service behavior, and API contracts
- updated architecture, API, schema, runbook, and plan documentation

## Explicitly excluded

- Playwright or browser fallback
- attachment discovery and Tika parsing
- OpenSearch indexing
- claim drafting, verification, and report generation
- worker scheduling, retry orchestration, and queue execution
