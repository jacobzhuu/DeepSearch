# Wikipedia Extraction And Claim Diagnostics Regression Fix

## 1. Objective

Fix the real-regression path where the SearXNG Wikipedia page is parsed as empty, then claim drafting fails with no diagnostics. The change keeps the current synchronous no-LLM pipeline but restores Wikipedia main-content extraction, adds explainable no-claim diagnostics, and permits a narrow fallback for explanatory answer sentences only.

## 2. Why this exists

The current real task for `What is SearXNG and how does it work?` reached acquisition, parsing, indexing, and claim drafting, but Wikipedia extraction returned `empty_extracted_text` from an 89 KB HTML page. The only remaining docs chunk was too short, so strict query-aware claim drafting produced zero claims and the pipeline failure details were null. This weakens operator recoverability and hides whether the failure is parsing, source coverage, or claim-filter strictness.

## 3. Scope

### In scope

- Wikipedia-specific HTML main-content extraction using `main`, `article`, `#content`, `#bodyContent`, and `.mw-parser-output` without deleting core paragraphs.
- Fallback extraction for Wikipedia-like pages when strict extraction returns empty.
- Extractor metadata for strategy, fallback, removed boilerplate count, and extracted text length.
- Claim-drafting diagnostics when no claim is produced, including chunk summaries and rejected candidate scoring/reasons.
- Narrow fallback claim selection for explanatory definition/mechanism/privacy sentences.
- Focused tests for extractor behavior, claim diagnostics, fallback, and an end-to-end drafting service scenario.
- Documentation updates for parse metadata and no-claim diagnostics.

### Out of scope

- No LLM, worker, LangGraph, browser fallback, Tika, schema migration, or state-machine change.
- No broad parser rewrite.
- No low-quality slogan fallback claims.
- No frontend feature change unless build compatibility requires a minimal fix.

## 4. Constraints

- Preserve citation-span validation and claim evidence traceability.
- Keep side effects idempotent through existing repositories and uniqueness boundaries.
- Keep task status transitions unchanged.
- Preserve existing SearXNG validator, proxy support, acquisition fallback, source priority, redirect-stub filtering, source quality scoring, query-aware ranking, report filtering, failed-task run button, report controls, and smoke provider behavior.
- Store new diagnostics in existing failure details/events and existing metadata JSON only; do not add relational schema.

## 5. Relevant files and systems

- `services/orchestrator/app/parsing/extractors.py`
- `services/orchestrator/app/parsing/quality.py`
- `services/orchestrator/app/services/parsing.py`
- `services/orchestrator/app/claims/drafting.py`
- `services/orchestrator/app/claims/__init__.py`
- `services/orchestrator/app/services/claims.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `tests/unit/orchestrator/test_parsing_helpers.py`
- `tests/unit/orchestrator/test_parsing_service.py`
- `tests/unit/orchestrator/test_claim_drafting_helpers.py`
- `tests/unit/orchestrator/test_claim_drafting_service.py`
- `services/orchestrator/tests/test_debug_pipeline_api.py`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`

## 6. Milestones

### Milestone 1
- intent: restore Wikipedia HTML extraction.
- code changes: update extractor selection/removal/fallback and metadata.
- validation: focused parsing helper/service tests.

### Milestone 2
- intent: make no-claims failure diagnosable.
- code changes: expose candidate rejection diagnostics and include them in pipeline failure details.
- validation: focused claim/pipeline tests.

### Milestone 3
- intent: add safe fallback claim drafting.
- code changes: relaxed explanatory fallback only after strict filters produce no claims.
- validation: fallback tests and mixed source service test.

### Milestone 4
- intent: document and validate.
- code changes: docs and plan log updates.
- validation: requested backend and frontend commands.

## 7. Implementation log

- 2026-04-27 / session:
  - changes: plan created before implementation.
  - rationale: this regression spans parsing, claim service diagnostics, pipeline failure payloads, tests, and docs.
  - validation: focused tests later passed.
  - next: implement extractor and claim service changes.
- 2026-04-27 / session:
  - changes: fixed MediaWiki/Wikipedia extraction by preserving content containers, avoiding broad `toc` suppression on page-level classes, handling void HTML elements correctly, and carrying extractor metadata into parse chunk/decision payloads.
  - rationale: the real Wikipedia response was valid HTML, but boilerplate cleanup and HTML depth tracking could suppress or lose the article body before paragraph extraction.
  - validation: focused parsing helper/service tests passed.
  - next: finalize full validation.
- 2026-04-27 / session:
  - changes: added no-claims diagnostics, conservative `fallback_relaxed` drafting for explanatory sentences, pipeline failure details, focused tests, and docs updates.
  - rationale: when strict deterministic filters reject all candidates, the operator needs chunk/candidate evidence and rejection reasons; fallback must not turn slogans or contribution text into claims.
  - validation: focused claim, fallback, and debug pipeline tests passed.
  - next: run requested backend and frontend validation.

## 8. Validation

- `python3 -m pytest -q` - passed
- `python3 -m ruff check .` - passed
- `python3 -m black --check .` - passed
- `cd apps/web && npm run build` - passed
- `python3 -m pytest -q tests/unit/orchestrator/test_parsing_helpers.py tests/unit/orchestrator/test_parsing_service.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py services/orchestrator/tests/test_debug_pipeline_api.py` - passed

## 9. Risks and unknowns

- Wikipedia markup can vary; this fix targets standard MediaWiki content containers and paragraph fallback rather than a general web readability engine.
- Fallback claim drafting intentionally remains conservative and may still fail on sources that contain only slogans or navigation.
- Historical failed task rows remain failed; rerunning requires creating a fresh task or revising/resetting through existing operator workflow.

## 10. Rollback / recovery

- Revert this plan plus touched parsing, claim, pipeline, docs, and tests files.
- No migration rollback is needed.
- Existing stored snapshots and failed task events remain auditable.

## 11. Deferred work

- Richer debug persistence of all rejected candidate sentences in a dedicated ledger table.
- General-purpose readability extraction beyond MediaWiki-specific fallback.
- Multi-source claim synthesis beyond deterministic sentence candidates.
