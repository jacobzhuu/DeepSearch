# Query-Aware Claim Drafting And Report Selection

## 1. Objective

Make deterministic claim drafting and Markdown report synthesis select answer-relevant claims for the task query. The implementation adds rule-based query intent classification, candidate scoring, diversified claim ranking, and report-level filtering without changing the pipeline state machine or introducing LLM, worker, browser, or dependency changes.

## 2. Why this exists

The completed MVP loop can now fetch, parse, index, draft, verify, and report, but the current claim selector ranks some clean-but-unhelpful sentences above direct answers. For the query `What is SearXNG and how does it work?`, contribution calls-to-action and slogans can become supported claims while definition, mechanism, privacy, and feature sentences are present in the source chunks.

## 3. Scope

### In scope

- Rule-based query intent classification for “what is X and how does it work?” style queries.
- Deterministic sentence filters for imperatives, slogans, lowercase fragments, setup-only text, and community/contribution logistics.
- Claim candidate scoring fields stored in `claim.notes_json`.
- Top-K candidate ranking with source/content/query/answer quality components and light category diversification.
- Report filtering by persisted claim quality and query-answer scores.
- Report warning and method/source-scope counts for answer-relevant and excluded low-quality claims.
- Focused unit tests for claim filters, ranking, service persistence, and report filtering.
- Documentation updates for the changed claim/report behavior.

### Out of scope

- No LLM-backed drafting, reranking, or verification.
- No worker, queue, LangGraph, browser fallback, or Tika behavior.
- No database schema migration.
- No pipeline status transition changes.
- No frontend behavior change unless a test/build failure requires a minimal compatibility fix.

## 4. Constraints

- Preserve Phase 11 host-local / self-hosted route.
- Keep `research_task` as the product center.
- Preserve citation-span traceability for every persisted claim.
- Store new scoring metadata in existing `claim.notes_json`; do not add tables or columns.
- Keep filtering deterministic, explainable, and conservative.
- Do not silently include low-quality supported claims in the Executive Summary.

## 5. Relevant files and systems

- `services/orchestrator/app/claims/drafting.py`
- `services/orchestrator/app/claims/__init__.py`
- `services/orchestrator/app/services/claims.py`
- `services/orchestrator/app/reporting/markdown.py`
- `services/orchestrator/app/reporting/manifest.py`
- `services/orchestrator/app/services/reporting.py`
- `tests/unit/orchestrator/test_claim_drafting_helpers.py`
- `tests/unit/orchestrator/test_claim_drafting_service.py`
- `tests/unit/orchestrator/test_report_markdown.py`
- `tests/unit/orchestrator/test_report_synthesis_service.py`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`

## 6. Milestones

### Milestone 1
- intent: classify query intent and score candidate sentences deterministically.
- code changes: add claim categories, rejection rules, scoring helpers, and helper tests.
- validation: focused claim drafting helper tests.

### Milestone 2
- intent: use the scores during claim drafting and persist scoring metadata.
- code changes: draft from ranked candidates across eligible chunks, preserve citation validation and idempotent claim/evidence reuse.
- validation: claim drafting service tests for ranking and metadata.

### Milestone 3
- intent: make reports answer-focused.
- code changes: report preparation filters low-quality claims and renderer reports answer coverage/exclusion counts and warnings.
- validation: report markdown and synthesis tests.

### Milestone 4
- intent: update docs and validate the repository.
- code changes: API/schema/runbook notes and plan log update.
- validation: backend tests/lint/format and frontend build.

## 7. Implementation log

- 2026-04-27 / session:
  - changes: plan created before implementation.
  - rationale: this task spans claim helpers, claim service selection, report synthesis, tests, and docs.
  - validation: pending.
  - next: implement milestone 1.
- 2026-04-27 / session:
  - changes: implemented rule-based query intent classification, answer-aware candidate scoring, CTA/slogan/community filters, diversified top-K claim selection, persisted scoring notes, report-level answer-quality filtering, report coverage counts/warnings, focused tests, and docs.
  - rationale: keep the no-LLM synchronous MVP loop deterministic while making claims and reports answer the operator query instead of preserving clean but irrelevant source sentences.
  - validation: `python3 -m pytest -q` passed; `python3 -m ruff check .` passed; `python3 -m black --check .` passed; `cd apps/web && npm run build` passed.
  - next: re-run the real SearXNG task for `What is SearXNG and how does it work?` and inspect generated claims/report.

## 8. Validation

- `python3 -m pytest -q` - passed
- `python3 -m ruff check .` - passed
- `python3 -m black --check .` - passed
- `cd apps/web && npm run build` - passed

## 9. Risks and unknowns

- Deterministic lexical rules may still miss some valid answer sentences for unusual queries.
- Historical low-quality claims can remain in the ledger; regenerated reports should filter them from answer sections.
- Query intent support is intentionally narrow and starts with definition/mechanism questions instead of general semantic planning.

## 10. Rollback / recovery

- Revert this plan plus the touched code/docs/tests.
- No database rollback is needed because no schema changes are introduced.
- Regenerate Markdown reports after rollback if an operator wants artifact content to match the previous selection behavior.

## 11. Deferred work

- Richer query intent classes beyond definition/mechanism questions.
- Semantic deduplication across paraphrased claims.
- Explicit debug persistence of rejected candidates if a later phase adds a candidate ledger.
