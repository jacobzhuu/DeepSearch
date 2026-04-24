# Phase 8

## Goal

Introduce the first verification and conflict-handling slice of the orchestrator: reuse the Phase 6 retrieval layer and the Phase 7 claim plus citation ledger to attach `support` or `contradict` evidence, then resolve each processed claim into the minimum stable verification status set.

## Deliverables

- minimal verification helper seam for:
  - support vs contradict classification
  - verification-status resolution
  - explainable rationale generation
- thin repository, service, and API paths for:
  - `POST /claims/verify`
  - `GET /claims`
  - `GET /claim-evidence`
- `claim_evidence.relation_type` expansion from support-only usage to:
  - `support`
  - `contradict`
- `claim.verification_status` expansion from draft-only usage to:
  - `draft`
  - `supported`
  - `mixed`
  - `unsupported`
- exact citation-span validation preserved during verification
- tests for helper behavior, repository helpers, service behavior, API contracts, and narrow integration
- updated architecture, API, schema, runbook, and plan documentation

## Explicitly excluded

- report generation
- HTML or PDF export
- multi-round planner or gap-analyzer logic
- complex reranking or embeddings
- multi-model voting
- rich contradiction reasoning or final narrative synthesis
