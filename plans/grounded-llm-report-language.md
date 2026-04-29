# Grounded LLM Report Writer and Report Language

## 1. Objective

Add an optional grounded LLM Markdown report writer and target-language report rendering without changing the ledger schema.

## 2. Why this exists

The current report renderer is deterministic, English-only, and explicitly avoids LLM writing. P3 requires an LLM writer that can make the final report read like a synthesized research report, while preserving the repository rule that facts must come only from verified claims, evidence, and citation spans. P4 requires report language to be carried from task creation and respected by templates, prompts, and report sections.

## 3. Scope

### In scope

- Resolve report language from `constraints.report_language`, `report_language`, or `constraints.language`.
- Add backend support for a top-level `report_language` create/revise field by normalizing it into task constraints.
- Localize deterministic report headings and template text for `zh-CN` while preserving English fallback.
- Add an optional grounded LLM writer that receives only the prepared report claim/evidence bundle and returns structured report sections with claim/evidence id mappings.
- Validate LLM output ids against the allowed claim/evidence/citation span set and fall back to deterministic rendering when the LLM output is invalid or unavailable.
- Store report writer mode, language, and LLM writer status in the existing `report_artifact.manifest_json`.
- Update frontend task creation to default to `zh-CN`.
- Update docs and focused tests.

### Out of scope

- Database migrations or a report-job table.
- LLM claim drafting or LLM verification.
- Semantic entailment validation of LLM paraphrases beyond grounding prompts and id validation.
- HTML/PDF export.
- Multi-round gap analysis.

## 4. Constraints

- Preserve `research_task` as the primary product object.
- Keep report facts traceable to existing `claim`, `claim_evidence`, and `citation_span` rows.
- Do not send raw source documents beyond citation excerpts to the report LLM.
- Do not add production dependencies.
- Keep deterministic report generation available and backwards compatible when LLM report writing is disabled.
- Do not break existing report artifact reads.

## 5. Relevant files and systems

- `services/orchestrator/app/settings.py`
- `services/orchestrator/app/llm/*`
- `services/orchestrator/app/reporting/*`
- `services/orchestrator/app/services/reporting.py`
- `services/orchestrator/app/api/routes/reporting.py`
- `services/orchestrator/app/api/routes/pipeline.py`
- `services/orchestrator/app/api/routes/debug_pipeline.py`
- `services/orchestrator/app/api/routes/research_tasks.py`
- `services/orchestrator/app/api/schemas/research_tasks.py`
- `services/orchestrator/tests/test_reporting_api.py`
- `services/orchestrator/tests/test_research_tasks_api.py`
- `apps/web/src/pages/tasks/NewTaskPage.tsx`
- `apps/web/src/types/api.ts`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/architecture.md`

## 6. Milestones

### Milestone 1
- intent: establish language normalization and API task constraints.
- code changes: add report-language fields and helpers; set frontend default to `zh-CN`.
- validation: API create/revise tests and web build.

### Milestone 2
- intent: localize deterministic Markdown reports.
- code changes: pass report language through the report synthesis service and renderer; add Chinese headings/template text.
- validation: focused reporting test for `zh-CN` artifact output.

### Milestone 3
- intent: add optional grounded LLM report writer.
- code changes: add structured prompt/output parser, id validation, deterministic fallback, manifest metadata, and dependency summaries.
- validation: service test with fake LLM provider proving output uses LLM text plus claim/evidence mapping and rejects invalid ids.

### Milestone 4
- intent: update operator documentation.
- code changes: document environment flags, API language contract, writer fallback behavior, and no-schema provenance.
- validation: docs reviewed for consistency.

## 7. Implementation log

- 2026-04-29:
  - changes: created this ExecPlan after P0-P2 were completed and before implementing P3/P4.
  - rationale: the change crosses reporting, API, UI, and docs, and needs an explicit rollback story.
  - validation: pending.
  - next: implement language normalization and deterministic Chinese report rendering.
- 2026-04-29:
  - changes: added top-level `report_language` support, `zh-CN` frontend default, deterministic Chinese Markdown templates, optional grounded LLM report writer, report writer manifest metadata, env examples, docs, and focused tests.
  - rationale: satisfy P3/P4 without schema changes while keeping report facts grounded in existing claim/evidence/citation rows.
  - validation: backend focused tests, frontend build, targeted ruff/black, compileall, and diff check passed; targeted mypy still reports pre-existing errors outside this change path.
  - next: optional manual run with a real OpenAI-compatible provider and `LLM_REPORT_WRITER_ENABLED=true`.

## 8. Validation

- `python -m compileall -q services/orchestrator/app` — passed
- `pytest services/orchestrator/tests/test_reporting_api.py services/orchestrator/tests/test_research_tasks_api.py tests/unit/orchestrator/test_report_synthesis_service.py -q` — passed
- `python -m ruff check services/orchestrator/app/reporting/__init__.py services/orchestrator/app/services/reporting.py services/orchestrator/app/reporting/grounded_llm.py services/orchestrator/app/reporting/markdown.py services/orchestrator/app/api/routes/research_tasks.py services/orchestrator/app/api/routes/reporting.py services/orchestrator/app/api/routes/pipeline.py services/orchestrator/app/api/routes/debug_pipeline.py services/orchestrator/tests/test_reporting_api.py services/orchestrator/tests/test_research_tasks_api.py tests/unit/orchestrator/test_report_synthesis_service.py` — passed
- `python -m black --check services/orchestrator/app/reporting/__init__.py services/orchestrator/app/services/reporting.py services/orchestrator/app/reporting/grounded_llm.py services/orchestrator/app/reporting/markdown.py services/orchestrator/app/api/routes/research_tasks.py services/orchestrator/app/api/routes/reporting.py services/orchestrator/app/api/routes/pipeline.py services/orchestrator/app/api/routes/debug_pipeline.py services/orchestrator/tests/test_reporting_api.py services/orchestrator/tests/test_research_tasks_api.py tests/unit/orchestrator/test_report_synthesis_service.py` — passed
- `python -m mypy services/orchestrator/app/reporting services/orchestrator/app/services/reporting.py services/orchestrator/app/api/routes/reporting.py services/orchestrator/app/api/routes/research_tasks.py services/orchestrator/app/api/schemas/research_tasks.py services/orchestrator/app/api/schemas/reporting.py` — failed on pre-existing errors in `services/orchestrator/app/claims/drafting.py`, `services/orchestrator/app/research_quality/evidence.py`, and `services/orchestrator/app/planning/planner.py`
- `cd apps/web && npm run build` — passed
- `git diff --check` — passed

## 9. Risks and unknowns

- The LLM writer can be constrained by prompt and id validation, but this change does not add full semantic entailment checking for every paraphrase.
- Existing source claims may remain in the source language; deterministic localization translates report scaffolding, not evidence excerpts.
- LLM writer failures should not make reports unrecoverable; the deterministic renderer remains the recovery path.

## 10. Rollback / recovery

- Set `LLM_REPORT_WRITER_ENABLED=false` to return to deterministic report writing.
- Revert the code/docs/frontend changes if needed; no database rollback is required.
- Existing artifacts remain readable because writer metadata is stored only in optional manifest JSON keys.

## 11. Deferred work

- Semantic validator for LLM paraphrases.
- First-class report job events.
- Rich UI controls for report-language selection beyond the default.
- HTML/PDF export.
