# Phase 5

## Goal

Introduce the first parsing and chunking slice of the orchestrator: read existing `content_snapshot` objects, extract minimal text from `text/html` and `text/plain`, persist provenance-linked `source_document` rows, and persist stable `source_chunk` rows.

## Deliverables

- minimal snapshot-to-source provenance link on `source_document`
- snapshot object-store read support plus startup-time snapshot backend validation
- minimal HTML and plain-text extraction
- stable, explainable `paragraph_window_v1` chunking
- thin repository, service, and API paths for `POST /parse`, `GET /source-documents`, and `GET /source-chunks`
- tests for parsing helpers, repositories, service behavior, API contracts, storage behavior, and migration behavior
- updated architecture, API, schema, runbook, and plan documentation
- explicit documentation that `source_document` is current-state, not a parse-history version chain
- explicit documentation of the stable parse-result `reason` enum

## Explicitly excluded

- Tika, PDF parsing, Office parsing, and attachment handling
- OpenSearch indexing or retrieval over chunks
- claim drafting, verification, and report generation
- worker scheduling, retry orchestration, and queue execution
