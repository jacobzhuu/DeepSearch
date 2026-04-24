# Phase 6

## Goal

Introduce the first indexing and retrieval slice of the orchestrator: take persisted `source_chunk` rows, upsert them into a task-scoped index backend, and expose thin debug APIs for indexed-chunk inspection and retrieval.

## Deliverables

- minimal chunk-index backend abstraction
- OpenSearch REST implementation without a new production dependency
- startup-time index backend configuration validation
- thin repository, service, and API paths for:
  - `POST /index`
  - `GET /indexed-chunks`
  - `GET /retrieve`
- task-scoped indexed document shape with stable traceability back to `source_chunk`
- basic paging for indexed-chunk listing and retrieval
- tests for backend behavior, repository helpers, service behavior, API contracts, and startup validation
- updated architecture, API, schema, runbook, and plan documentation

## Explicitly excluded

- claim drafting
- citation-span binding
- verification
- report generation
- embeddings, hybrid retrieval, and reranking
- worker scheduling, queue execution, and retry orchestration
- Tika or attachment parsing
