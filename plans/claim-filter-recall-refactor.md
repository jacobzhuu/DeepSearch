# Claim Filter Recall Refactor

## 1. Objective

Finish the partial claim-filtering refactor so early deterministic filters preserve useful short
definition, component, and mechanism evidence while report synthesis remains strict about verified
support and reviewer roles.

## 2. Why this exists

The old claim pipeline rejected too many short but meaningful spans before semantic scoring or LLM
review could evaluate them. This particularly starved concept-definition questions such as
`什么是 transformer 架构？`, causing too few candidate claims and short reports despite adequate
source material.

## 3. Scope

### In scope

- Audit and complete the partially applied claim-scoring and LLM-review schema migration.
- Keep hard filtering limited to fatal garbage.
- Add candidate tiers and source-suitability scoring to diagnostics and callers.
- Maintain compatibility with old `accept` / `downrank` / `covered_slot_ids` reviewer outputs.
- Keep final reports restricted to verified, report-eligible claims.
- Add focused regression tests for Chinese transformer definition/mechanism recall, reviewer
  compatibility, source suitability, and report safety.

### Out of scope

- New database schema or migrations.
- Rewriting verification, retrieval, planner, acquisition, or the full report renderer.
- Making recall candidates report-ready without verification and reviewer/report eligibility.
- Docker-first packaging or unrelated UI cleanup.

## 4. Constraints

- No new production dependencies.
- Preserve existing persisted claim notes and older report artifacts.
- `research_task` remains the product center.
- Weak, unsupported, mixed, contradicted, example-only, context-only, or recall-only claims must not
  become established factual report conclusions.
- Existing dirty worktree changes outside this refactor must not be reverted.

## 5. Relevant files and systems

- `services/orchestrator/app/claims/drafting.py`
- `services/orchestrator/app/research_quality/llm_assistance.py`
- `services/orchestrator/app/services/claims.py`
- `services/orchestrator/app/services/reporting.py`
- `services/orchestrator/app/reporting/markdown.py`
- `services/orchestrator/app/reporting/grounded_llm.py`
- `tests/unit/orchestrator/test_claim_drafting_helpers.py`
- `tests/unit/orchestrator/test_llm_assistance.py`
- `tests/unit/orchestrator/test_report_synthesis_service.py`
- claim-related service/API tests under `tests/unit/orchestrator` and `services/orchestrator/tests`

## 6. Milestones

### Milestone 1
- intent: establish the actual repository state and broken compatibility points.
- code changes: none.
- validation: compile/grep current claim and reviewer code; inspect report selection callers.

### Milestone 2
- intent: complete deterministic claim recall and scoring migration.
- code changes: tune hard-filter analysis, Chinese/English concept intent handling, source
  suitability, caller `domain` propagation, candidate-tier diagnostics, and legacy score-note
  reconstruction.
- validation: focused claim helper/service tests and the transformer regression.

### Milestone 3
- intent: complete LLM reviewer compatibility and report safety.
- code changes: normalize old and new reviewer decisions into the new schema, add safe report
  adapter logic for reviewer roles, and expose reviewer/report exclusion diagnostics where the
  existing pipeline records them.
- validation: LLM assistance and report synthesis tests.

### Milestone 4
- intent: close the turn with documented, validated state.
- code changes: update this plan implementation log and relevant runbook note if behavior changed.
- validation: requested focused pytest commands plus claim-related tests and ruff check/format.

## 7. Implementation log

- 2026-05-08 audit:
  - Read the governing repo docs in the required order. All required docs were present.
  - Confirmed `services/orchestrator/app/llm_assistance.py` does not exist in this checkout; the
    active implementation is `services/orchestrator/app/research_quality/llm_assistance.py`.
  - `python -m compileall services/orchestrator/app/claims services/orchestrator/app/llm_assistance.py`
    compiled the claim package but failed to list the nonexistent `app/llm_assistance.py`.
  - Corrected compile check with `python -m compileall services/orchestrator/app/claims` plus
    `python -m py_compile` for `research_quality/llm_assistance.py`, `services/claims.py`, and
    `services/reporting.py`; no syntax errors were reported.
  - Found partial migration state: `ClaimAnalysis`, `source_suitability_score`,
    `candidate_tier`, and new reviewer fields exist, but callers do not pass source domains,
    report score reconstruction still omits new dataclass fields, old reviewer tests and report
    filtering still use old `accept` / `downrank` semantics, and Chinese concept intent is still
    classified as `generic`.
  - Next: implement the scoped fixes and add focused regressions.
- 2026-05-08 implementation:
  - completed the deterministic hard-filter/scoring migration in `drafting.py`: Chinese
    definition/mechanism intent is recognized, Chinese and English explanatory verbs/patterns
    are scored, short/missing-punctuation/heading-like/fragment/code-like text is represented as
    soft flags, fatal garbage remains rejected, and source suitability is distinct from generic
    source quality.
  - propagated source domain/URL into claim scoring, added tier/soft-flag/source-suitability
    diagnostics, restored stable figure/diagram rejection reason keys, and made legacy
    `ClaimCandidateScore` reconstruction in reports supply the new fields.
  - normalized old and new LLM claim-review outputs into the new `keep_*` schema, mapped
    `covered_slot_ids` into `related_answer_slot`, downgraded unsafe `keep_main` decisions, and
    added reviewer decision-count diagnostics.
  - tightened report eligibility so `keep_example` and `keep_context` cannot support factual
    conclusions, weak support remains outside main answer sections, and report filter counts are
    recorded in report-writer metadata.
  - updated focused regression tests and the runbook claim/reviewer behavior note.
  - validation: all targeted tests, claim-related tests, compile checks, ruff check, and ruff
    format passed using the actual reviewer module path
    `services/orchestrator/app/research_quality/llm_assistance.py`.

## 8. Validation

- `python -m compileall services/orchestrator/app/claims services/orchestrator/app/llm_assistance.py`
  - partial: claim package compiled; nonexistent `app/llm_assistance.py` path failed as expected
    for this checkout.
- `python -m compileall services/orchestrator/app/claims && python -m py_compile services/orchestrator/app/research_quality/llm_assistance.py services/orchestrator/app/services/claims.py services/orchestrator/app/services/reporting.py`
  - passed during audit.
- `pytest tests/unit/orchestrator/test_claim_drafting_helpers.py -q` — passed.
- `pytest tests/unit/orchestrator/test_llm_assistance.py -q` — passed.
- `pytest tests/unit/orchestrator/test_report_synthesis_service.py -q` — passed.
- `pytest tests/unit/orchestrator/test_claim_drafting_service.py -q` — passed after restoring
  stable figure/diagram diagnostic reason keys.
- `pytest tests/unit/orchestrator/test_claim_verification_service.py tests/unit/orchestrator/test_claim_verification_helpers.py -q`
  — passed.
- `pytest services/orchestrator/tests/test_claims_api.py services/orchestrator/tests/test_claim_verification_api.py services/orchestrator/tests/test_deployment_claim_quality.py -q`
  — passed.
- `pytest tests/unit/orchestrator/test_llm_assistance.py tests/unit/orchestrator/test_report_synthesis_service.py tests/unit/orchestrator/test_search_helpers.py tests/unit/orchestrator/test_evidence_quality.py services/orchestrator/tests/test_debug_pipeline_api.py -q`
  — passed.
- `pytest tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_claim_verification_service.py tests/unit/orchestrator/test_claim_verification_helpers.py services/orchestrator/tests/test_claims_api.py services/orchestrator/tests/test_claim_verification_api.py services/orchestrator/tests/test_deployment_claim_quality.py -q`
  — passed.
- `python -m ruff check services/orchestrator/app/claims/drafting.py services/orchestrator/app/research_quality/llm_assistance.py services/orchestrator/app/services services/orchestrator/app/reporting tests/unit/orchestrator --fix`
  — passed after line-length fixes.
- `python -m ruff format services/orchestrator/app/claims/drafting.py services/orchestrator/app/research_quality/llm_assistance.py services/orchestrator/app/services/claims.py services/orchestrator/app/services/reporting.py services/orchestrator/app/reporting tests/unit/orchestrator`
  — passed.

## 9. Risks and unknowns

- The worktree already contains many unrelated modified files. This plan will not attempt to
  classify or revert them.
- The existing report diagnostics are built only from included report claims, so excluded-report
  counts may need to live in claim notes or report-writer metadata rather than a relational table.
- LLM reviewer decisions are advisory and optional; deterministic verifier/report safety remains
  authoritative.

## 10. Rollback / recovery

- Revert this plan and the scoped edits in the files listed above.
- No schema migration is involved.
- Existing persisted rows remain readable because compatibility code keeps old claim-note and
  reviewer fields accepted.

## 11. Deferred work

- A richer heading-context carrier for non-factual headings.
- Dedicated relational tables for reviewer decisions or evidence-candidate lifecycle if JSON seams
  become too large for operator review.
- Semantic entailment verification beyond the current deterministic lexical verifier.
