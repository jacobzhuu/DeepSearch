# Phase 10

## Goal

Harden the existing Phase 2 through Phase 9 research kernel for real-infrastructure integration on the host-local / self-hosted Linux path: add a MinIO-capable object-store backend, stronger OpenSearch validation and error handling, minimum JSON logging plus metrics, and `report_artifact` content-hash plus manifest provenance without changing the main product API semantics. Docker or compose may exist as optional tooling, but they are not the primary Phase 10 acceptance target.

## Deliverables

- one reversible migration for `report_artifact.content_hash` and `report_artifact.manifest_json`
- report synthesis persistence of:
  - Markdown `content_hash`
  - minimal report manifest / provenance snapshot
- object-store seam that now supports:
  - `filesystem`
  - `minio`
- startup-time configuration validation for:
  - snapshot/report storage backend
  - MinIO bucket availability
  - OpenSearch configuration
  - optional live OpenSearch connectivity
- minimum observability surface:
  - JSON logs
  - `/metrics`
  - task, fetch, parse, verify, and report counters
- narrow real-infrastructure validation guidance for:
  - PostgreSQL
  - MinIO
  - OpenSearch
- host-local / self-hosted Linux remains the primary validation path
- Docker / compose remains optional deployment packaging only
- updated architecture, API, schema, runbook, and plan documentation

## Explicitly excluded

- OpenClaw integration
- HTML or PDF export
- new planner or gap-analyzer behavior
- new claim-drafting or verifier semantics
- new search, fetch, parse, or retrieval product capabilities
- worker orchestration or queue semantics
- dashboarding, tracing, or advanced alerting systems
