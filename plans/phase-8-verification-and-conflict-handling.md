# Phase 8 Verification And Conflict Handling

## 1. Objective

Implement the first verification slice on top of Phase 7 claim drafting: evaluate existing task-scoped draft claims against retrieved chunk candidates, persist minimal `support` and `contradict` evidence relations, and update each claim to a stable verification status with an explainable evidence bundle summary.

## 2. Why this exists

Phase 7 can draft support-only claims with exact citation spans, but the system still cannot aggregate conflicting evidence or produce a stable verification outcome. Phase 8 needs the thinnest possible verifier so later reporting can consume persisted `claim`, `citation_span`, and `claim_evidence` records instead of inferring support quality ad hoc.

## 3. Scope

### In scope

- add minimal verification helpers for:
  - support vs contradict classification
  - verification-status resolution
  - human-readable rationale generation
- verify existing task claims through the current retrieval seam
- expand claim evidence handling from `support` only to:
  - `support`
  - `contradict`
- update persisted `claim.verification_status` to the minimum Phase 8 set:
  - `draft`
  - `supported`
  - `mixed`
  - `unsupported`
- preserve exact citation-span validation when verification adds new evidence
- add thin repository helpers for task-scoped claim selection and evidence filtering
- add thin APIs for:
  - `POST /api/v1/research/tasks/{task_id}/claims/verify`
  - `GET /api/v1/research/tasks/{task_id}/claims`
  - `GET /api/v1/research/tasks/{task_id}/claim-evidence`
- expose a minimal evidence-bundle summary through the existing claim read API
- update tests, docs, and this plan

### Out of scope

- report generation, HTML export, or PDF export
- multi-round planner or gap-analyzer logic
- complex reranking, embeddings, or multi-model voting
- schema changes unless implementation proves a minimal verification constraint tightening is necessary
- citation-span generation beyond exact chunk-local spans

## 4. Constraints

- stay strictly within Phase 8
- preserve current Phase 2 through Phase 7 API behavior and additive compatibility
- keep verification deterministic and explainable
- keep verification built on the current retrieval, citation-span, and claim-evidence seams
- expand `claim_evidence.relation_type` only to `support` and `contradict`
- keep `claim.verification_status` limited to `draft`, `supported`, `mixed`, and `unsupported`
- do not implement report generation or richer conflict resolution
- do not add a new production dependency

## 5. Relevant files and systems

- `services/orchestrator/app/claims/`
- `services/orchestrator/app/services/claims.py`
- `services/orchestrator/app/indexing/`
- `services/orchestrator/app/api/routes/claims.py`
- `services/orchestrator/app/api/schemas/claims.py`
- `services/orchestrator/app/main.py`
- `services/orchestrator/app/settings.py`
- `packages/db/repositories/claims.py`
- `packages/db/repositories/sources.py`
- `packages/db/models/ledger.py`
- `tests/unit/db/`
- `tests/unit/orchestrator/`
- `services/orchestrator/tests/`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/architecture.md`
- `docs/phases/phase-8.md`

## 6. Milestones

### Milestone 1
- intent: define deterministic verification helpers and the minimal stable Phase 8 enums
- code changes:
  - claim verification helper module
  - support vs contradict classification helpers
  - verification status resolution helpers
  - helper unit tests
- validation:
  - helper-focused unit tests

### Milestone 2
- intent: persist verification results through minimal repository and service seams
- code changes:
  - repository helpers for claim selection and evidence filtering
  - verification service additions
  - repository and service tests
- validation:
  - repo tests
  - service tests

### Milestone 3
- intent: expose the minimal verification and read APIs for operators
- code changes:
  - route and schema updates
  - app router wiring if needed
  - API tests
- validation:
  - API tests

### Milestone 4
- intent: keep docs synchronized and validate the full narrow verification path
- code changes:
  - update architecture, API, schema, runbook, phase doc, and this plan
- validation:
  - lint, format, type checks, full pytest
  - narrow manual flow: create -> seed or retrieve source chunks -> index -> claims/draft -> claims/verify -> claims -> claim-evidence

## 7. Implementation log

- 2026-04-23 research: reread `AGENTS.md`, `deep_research_codex_dev_spec.md`, `PLANS.md`, `code_review.md`, and current docs. Confirmed Phase 8 should stop at minimal verification and conflict handling, without report generation, HTML/PDF export, planner work, or complex reranking.
- 2026-04-23 implementation: added deterministic verification helpers under `services/orchestrator/app/claims/verification.py`, including support vs contradict span classification, stable verification-status resolution, and minimal rationale generation.
- 2026-04-23 implementation: extended the claims service and repository seams to select task claims, add `contradict` evidence, aggregate support and contradict counts, update `claim.verification_status`, and persist a minimal verification bundle in `claim.notes_json["verification"]`.
- 2026-04-23 implementation: added `POST /claims/verify`, extended `GET /claims` with verification summaries, extended `GET /claim-evidence` with optional `relation_type` filtering, and updated docs plus the Phase 8 phase document.
- 2026-04-23 validation: helper, repository, service, and API tests passed. Full lint, format, type checks, full pytest, and a narrow manual API chain then passed in the same turn. The manual API path used temporary SQLite plus a dependency-overridden in-memory retrieval backend and explicitly verified `excerpt == source_chunk.text[start_offset:end_offset]` for the contradict evidence returned by `GET /claim-evidence`.

## 8. Validation

- completed:
  - `python3 -m pytest tests/unit/orchestrator/test_claim_verification_helpers.py tests/unit/orchestrator/test_claim_verification_service.py services/orchestrator/tests/test_claim_verification_api.py tests/unit/db/test_repositories.py -q` — passed
  - `python3 -m pytest tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_claim_verification_helpers.py tests/unit/orchestrator/test_claim_verification_service.py services/orchestrator/tests/test_claims_api.py services/orchestrator/tests/test_claim_verification_api.py tests/unit/db/test_repositories.py -q` — passed
  - `python3 -m ruff check .` — passed
  - `python3 -m black --check .` — passed
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit` — passed
  - `python3 -m pytest` — passed (`79 passed`)
  - `rm -f /tmp/deepresearch_phase8_manual.db && DATABASE_URL=sqlite:////tmp/deepresearch_phase8_manual.db python3 -m alembic -c alembic.ini upgrade head` followed by a narrow `TestClient` API chain for `POST /research/tasks`, `POST /claims/draft`, `POST /claims/verify`, `GET /claims`, and `GET /claim-evidence?relation_type=contradict` — passed
  - manual verification output confirmed:
    - `task_status = PLANNED`
    - `verification_status = mixed`
    - `support_evidence_count = 1`
    - `contradict_evidence_count = 1`
    - `excerpt_matches_chunk_slice = True`

- known unvalidated areas before implementation:
  - real OpenSearch cluster behavior
  - PostgreSQL-specific behavior
  - docker-based validation because `docker` is unavailable in the current environment

## 9. Risks and unknowns

- contradiction detection must stay conservative; false contradiction is worse than missing a weak contradiction hit in this phase
- existing schema stores free-form strings for `verification_status` and `relation_type`; if no migration is added, service and docs must remain explicit
- repeated verification calls need enough idempotency guard to avoid noisy duplicate evidence links
- `GET /claims` must remain compatible while surfacing a minimal verification bundle

## 10. Rollback / recovery

- revert the Phase 8 helper, service, route, schema, test, and doc changes
- if a migration is introduced, document the exact downgrade command and data caveats before finishing the turn

## 11. Deferred work

- richer contradiction reasoning and cross-source conflict resolution
- verifier-driven citation-span refinement beyond sentence-like spans
- report composition and narrative synthesis
- semantic claim deduplication beyond exact statement reuse
- planner-driven re-search or gap analysis
