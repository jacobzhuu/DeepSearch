# Phase 7

## Goal

Introduce the first claim-drafting and citation-binding slice of the orchestrator: select evidence chunks from retrieval or explicit ids, draft minimal support-only claims, validate exact citation offsets, and persist `claim`, `citation_span`, and `claim_evidence` rows.

## Deliverables

- minimal claim-drafting helper seam with deterministic sentence selection
- explicit citation span validation against `source_chunk.text`
- thin repository, service, and API paths for:
  - `POST /claims/draft`
  - `GET /claims`
  - `GET /claim-evidence`
- support-only evidence relation handling
- draft-only verification status handling
- tests for helper behavior, repository helpers, service behavior, API contracts, and narrow integration
- updated architecture, API, schema, runbook, and plan documentation

## Explicitly excluded

- verification
- report generation
- HTML or PDF export
- contradiction handling or mixed judgments
- multi-model voting
- complex reranking
- multi-round planner or gap-analyzer logic
