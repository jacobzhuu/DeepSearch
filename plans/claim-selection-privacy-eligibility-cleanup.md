# Claim Selection And Privacy Eligibility Cleanup

## 1. Objective

Tighten deterministic claim selection for the SearXNG what/how query so setup or broken-link instruction sentences do not become claims or report conclusions, while preserving Privacy article sentences that share a chunk with trailing `See also` or `References` headings.

## 2. Why this exists

The current real run completes, but a setup sentence with missing link text is promoted into claims and the report. At the same time, a Wikipedia chunk containing valid privacy prose is marked as a reference section because it ends with `See also` and `References`. This hurts answer quality without requiring any new pipeline state, LLM, browser fallback, or schema work.

## 3. Scope

### In scope

- Reject setup/getting-started/instruction and broken-link residue claim candidates for definition/mechanism queries.
- Keep whole reference chunks ineligible only when the chunk starts with references or is mostly reference material.
- Allow privacy prose before trailing `See also` / `References` headings to remain claim-eligible.
- Filter supported report claims by category for definition/mechanism queries.
- Add focused unit/service/report tests and update docs.

### Out of scope

- No LLM, worker, LangGraph, browser fallback, state-machine changes, schema migrations, or broad parser rewrite.
- No new production dependencies.
- No frontend behavior change beyond running the existing build validation.

## 4. Constraints

- Preserve citation-span exactness and existing claim evidence persistence.
- Preserve Wikipedia extraction, no-claims diagnostics, strict/fallback claim logic, source quality scoring, acquisition fallback, proxy trace, SSRF guard, report controls, and smoke provider behavior.
- Store any behavior changes in existing metadata/notes only; no new database columns or tables.

## 5. Relevant files and systems

- `services/orchestrator/app/parsing/quality.py`
- `services/orchestrator/app/claims/drafting.py`
- `services/orchestrator/app/services/claims.py`
- `services/orchestrator/app/services/reporting.py`
- `tests/unit/orchestrator/test_parsing_helpers.py`
- `tests/unit/orchestrator/test_claim_drafting_helpers.py`
- `tests/unit/orchestrator/test_claim_drafting_service.py`
- `tests/unit/orchestrator/test_report_synthesis_service.py`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`

## 6. Milestones

### Milestone 1
- intent: refine reference-section chunk eligibility.
- code changes: make reference detection whole-section aware, not tail-heading based.
- validation: parsing helper tests for reference-only and privacy-plus-tail chunks.

### Milestone 2
- intent: reject bad setup and broken-link candidates.
- code changes: add deterministic sentence rejection rules and preserve expected privacy candidates.
- validation: claim helper/service tests.

### Milestone 3
- intent: keep report summaries answer-focused.
- code changes: filter supported claims by category/answer quality for definition/mechanism queries.
- validation: report synthesis tests.

### Milestone 4
- intent: document and validate.
- code changes: update docs and this plan.
- validation: requested backend and frontend commands.

## 7. Implementation log

- 2026-04-27 / session:
  - changes: plan created before implementation.
  - why: changes span parsing quality, claim drafting, report synthesis, tests, and docs.
  - validation: focused tests later passed.
  - next: implement milestones 1-3.
- 2026-04-27 / session:
  - changes: refined reference-section detection, rejected setup/broken-link claim candidates, added privacy terms, fixed mechanism sentence false citation detection, and added report category gating for definition/mechanism queries.
  - why: completed SearXNG runs were still promoting a setup instruction while rejecting valid privacy prose.
  - validation: focused parsing, claim helper, claim service, and report synthesis tests passed.
  - next: full validation completed.

## 8. Validation

- `python3 -m pytest -q` - passed
- `python3 -m ruff check .` - passed
- `python3 -m black --check .` - passed
- `cd apps/web && npm run build` - passed
- `python3 -m pytest -q tests/unit/orchestrator/test_parsing_helpers.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_report_synthesis_service.py` - passed

## 9. Risks and unknowns

- Reference-section detection remains heuristic; the goal is to avoid whole-chunk false positives without treating reference paragraphs as claims.
- Existing completed tasks keep already persisted claims/reports; a new task is needed to verify improved generation.

## 10. Rollback / recovery

- Revert this plan and touched parsing, claim, reporting, docs, and tests files.
- No migration rollback is needed.

## 11. Deferred work

- Sentence-level removal of trailing references from persisted chunk text.
- Dedicated parse-history ledger for cleaned text variants.
