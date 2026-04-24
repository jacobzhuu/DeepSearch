# Phase 7 Claim Drafting And Citation Binding

## 1. Objective

Implement the first claim-drafting slice on top of Phase 6 retrieval: draft minimal task-scoped claims from retrieved or explicitly selected `source_chunk` rows, bind each drafted claim to a validated `citation_span`, and persist `claim` plus `claim_evidence` rows through the existing ledger.

## 2. Why this exists

Phase 6 can retrieve relevant source chunks, but the system still cannot turn retrieved evidence into auditable claim drafts. Phase 7 needs the thinnest possible claim path that preserves evidence traceability, so later verification and reporting phases can build on persisted draft claims instead of skipping directly from retrieval to final outputs.

## 3. Scope

### In scope

- add a minimal claim-drafting helper seam for deterministic sentence selection and citation-span validation
- draft claims from either:
  - a task-scoped retrieval query
  - explicitly selected `source_chunk` ids
- persist minimal `claim` rows with:
  - `statement`
  - `claim_type`
  - `confidence`
  - `verification_status`
- bind drafted claims to validated `citation_span` rows
- persist `claim_evidence` rows with `relation_type = support` only
- add thin repository helpers for task-scoped claim, citation-span, and claim-evidence lookup
- add thin APIs for:
  - `POST /api/v1/research/tasks/{task_id}/claims/draft`
  - `GET /api/v1/research/tasks/{task_id}/claims`
  - `GET /api/v1/research/tasks/{task_id}/claim-evidence`
- explicitly document current claim and citation limits
- add unit, repository, service, and API tests

### Out of scope

- verification semantics or verifier status transitions
- contradiction handling, mixed judgments, or conflict resolution
- report generation, HTML export, or PDF export
- multi-round planner or gap-analyzer logic
- complex reranking, multi-model voting, or LLM orchestration
- schema changes unless implementation proves one is necessary

## 4. Constraints

- stay strictly within Phase 7
- preserve current Phase 2 through Phase 6 API behavior
- keep claim drafting deterministic and explainable
- support only `support` claim evidence relations in this phase
- keep `verification_status` in a draft-only state; do not imply verification happened
- citation spans must remain traceable to `source_chunk` and be validated against exact chunk text
- prefer service-level correctness over speculative abstraction
- do not introduce a new production dependency

## 5. Relevant files and systems

- `services/orchestrator/app/indexing/`
- `services/orchestrator/app/services/indexing.py`
- `services/orchestrator/app/services/`
- `services/orchestrator/app/api/routes/`
- `services/orchestrator/app/api/schemas/`
- `packages/db/models/ledger.py`
- `packages/db/repositories/claims.py`
- `packages/db/repositories/sources.py`
- `tests/unit/db/`
- `tests/unit/orchestrator/`
- `services/orchestrator/tests/`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/architecture.md`
- `docs/phases/phase-7.md`

## 6. Milestones

### Milestone 1
- intent: define deterministic draft-claim and citation-span helpers with explicit span validation
- code changes:
  - claim helper module
  - stable Phase 7 constants
  - helper unit tests
- validation:
  - helper-focused unit tests

### Milestone 2
- intent: persist claims, citation spans, and claim evidence through minimal repository and service seams
- code changes:
  - repository helpers
  - claim-drafting service
  - service and repository tests
- validation:
  - repo tests
  - service tests

### Milestone 3
- intent: expose the minimal draft and read APIs for operators
- code changes:
  - route and schema wiring
  - API tests
  - app router update
- validation:
  - API tests

### Milestone 4
- intent: keep docs synchronized and verify the full narrow claim-drafting path
- code changes:
  - update architecture, API, schema, runbook, phase doc, and this plan
- validation:
  - lint, format, type checks, full pytest
  - narrow manual flow: create -> search -> fetch -> parse -> index -> claims/draft -> claims -> claim-evidence

## 7. Implementation log

- 2026-04-23 research: reread `AGENTS.md`, `deep_research_codex_dev_spec.md`, `PLANS.md`, `code_review.md`, and current docs. Confirmed Phase 7 should stop at minimal support-only claim drafting and citation binding, with no verifier, report, or multi-round planning logic.
- 2026-04-23 implementation: added deterministic claim-drafting helpers under `services/orchestrator/app/claims/`, including support-only constants, sentence-like span selection, minimal confidence scoring, normalized excerpt hashing, and explicit citation offset plus excerpt validation against `source_chunk.text`.
- 2026-04-23 implementation: added repository helpers for exact statement lookup, exact citation span lookup, and task-scoped claim-evidence reads; then added `ClaimDraftingService` plus Phase 7 APIs for `POST /claims/draft`, `GET /claims`, and `GET /claim-evidence`.
- 2026-04-23 validation: helper, repository, service, and API tests passed. Full lint, format, type checks, full pytest, and narrow manual claim-drafting integration then passed in the same turn. The manual API chain covered task create, search, fetch, parse, index, claims draft, claims list, and claim-evidence list against temporary SQLite plus fake SearXNG and fake OpenSearch services, and explicitly verified `excerpt == source_chunk.text[start_offset:end_offset]`.

## 8. Validation

- planned:
  - `python3 -m ruff check .`
  - `python3 -m black --check .`
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit`
  - `python3 -m pytest`
  - narrow manual API chain against temporary SQLite plus fake SearXNG and fake OpenSearch services

- completed so far:
  - `python3 -m pytest tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py services/orchestrator/tests/test_claims_api.py tests/unit/db/test_repositories.py -q` — passed
  - `python3 -m pytest tests/unit/orchestrator/test_claim_drafting_service.py services/orchestrator/tests/test_claims_api.py -q` — passed
  - `python3 -m ruff check .` — passed
  - `python3 -m black --check .` — passed
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit` — passed
  - `python3 -m pytest` — passed (`71 passed`)
  - manual API chain with fake SearXNG on `127.0.0.1:18080`, fake OpenSearch on `127.0.0.1:19200`, SQLite at `/tmp/deepresearch_phase7_manual.db`, and filesystem snapshots at `/tmp/deepresearch_phase7_snapshots` — passed

- known unvalidated areas before implementation:
  - real OpenSearch cluster behavior
  - PostgreSQL-specific behavior
  - docker-based validation because `docker` is unavailable in the current environment

## 9. Risks and unknowns

- claim drafting must stay traceable without drifting into unverifiable summarization
- citation span validation needs to reject offset and excerpt mismatches before they enter the ledger
- current schema allows broader claim statuses and evidence relations than Phase 7 will use; service-layer restrictions must remain explicit if no migration is added
- repeated draft calls need enough idempotency guard to avoid noisy duplicate claims

## 10. Rollback / recovery

- revert the Phase 7 claim helper, service, route, schema, test, and doc changes
- if a migration is introduced later, document the exact downgrade command and data caveats before finishing the turn

## 11. Deferred work

- contradiction evidence and mixed judgments
- verifier status transitions
- report composition from drafted claims
- claim deduplication beyond exact statement matching
- multi-hop claim synthesis across multiple chunks
