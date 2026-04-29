# LLM Provider Abstraction And Research Planner V1

## 1. Objective

Introduce an optional LLM provider abstraction and Research Planner v1 that can produce a bounded research plan before search, without changing the existing deterministic fetch/parse/index/claim/verify/report loop or using an LLM for final report writing.

## 2. Why this exists

The deterministic MVP now completes real research tasks. The next useful increment is query planning: decompose a question into subquestions and search queries while preserving the evidence-first deterministic pipeline. This must remain optional and no-LLM by default so the current real-search + OpenSearch baseline continues to work unchanged.

## 3. Scope

### In scope

- Add LLM configuration with safe defaults and API-key redaction.
- Add `NoopLLMProvider` and `OpenAICompatibleLLMProvider` with structured sanitized errors and mockable HTTP transport.
- Add Research Planner v1 data structures, JSON parsing, deterministic fallback/noop planning, and SearXNG-oriented planning output.
- Integrate planner before search only when enabled, emitting `research_plan.created` or `research_plan.failed` events.
- Let search discovery use bounded, deduped planner queries when a plan exists.
- Expose planner summary in task progress observability and the Task Detail UI.
- Add backend and frontend tests, docs, and validation.

### Out of scope

- No LangGraph, worker queue, browser fallback, PDF/Tika, database schema migration, or broad pipeline rewrite.
- No LLM-generated final report, claim, evidence, verification result, or report artifact.
- No real external LLM calls in tests.

## 4. Constraints

- `LLM_ENABLED=false` and `RESEARCH_PLANNER_ENABLED=false` must preserve the current deterministic baseline.
- API keys must come only from environment or `.env`; keys must not appear in logs, task events, API responses, reports, or test snapshots.
- Planner failures must not fail the pipeline; the run falls back to the original query.
- Store planner output only in existing `task_event.payload` JSON and runtime observability summaries; no relational schema changes.
- Keep search/fetch/parse/index/claim/verify/report services deterministic and evidence-first.

## 5. Relevant files and systems

- `services/orchestrator/app/settings.py`
- `services/orchestrator/app/llm/`
- `services/orchestrator/app/planning/`
- `scripts/smoke_deepseek_planner.py`
- `services/orchestrator/app/search/`
- `services/orchestrator/app/services/search.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/api/routes/research_tasks.py`
- `services/orchestrator/app/api/schemas/research_tasks.py`
- `apps/web/src/pages/tasks/TaskDetailPage.tsx`
- `apps/web/src/types/api.ts`
- backend unit and pipeline tests
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/architecture.md`

## 6. Milestones

### Milestone 1
- intent: add safe config and provider seam.
- code changes: settings, provider types, noop/openai-compatible providers, provider tests.
- validation: settings and LLM provider tests.

### Milestone 2
- intent: add Research Planner v1.
- code changes: plan dataclasses/Pydantic models, prompt builder, parser/fallback, planner service tests.
- validation: planner tests including SearXNG query expected fields.

### Milestone 3
- intent: integrate planner before search without changing state machine.
- code changes: debug pipeline pre-search hook, search query override/append support, events, observability summary.
- validation: pipeline tests for disabled, noop, generated queries, failure fallback, no key leakage.

### Milestone 4
- intent: expose planner in API/UI and docs.
- code changes: progress observability schema/types, Task Detail section, docs/runbook updates.
- validation: API tests and frontend build.

### Milestone 5
- intent: full validation.
- code changes: mechanical formatting only if needed.
- validation: requested backend and frontend commands.

## 7. Implementation log

- 2026-04-27 / session:
  - changes: plan created before implementation.
  - why: work spans config, provider seam, planner service, pipeline events, observability, UI, tests, and docs.
  - validation: pending.
  - next: inspect current settings/search/pipeline entry points.
- 2026-04-27 / session:
  - changes: added safe LLM/planner settings, noop and OpenAI-compatible providers, planner service, pre-search pipeline hook, planner query use in search discovery, task-detail observability, frontend plan display, env examples, docs, and tests.
  - why: keep the deterministic pipeline intact while allowing an optional planner-only LLM seam.
  - validation: targeted backend tests for providers, planner, search discovery, and pipeline planner behavior passed.
  - next: full backend and frontend validation.
- 2026-04-27 / session:
  - changes: full validation completed after formatting.
  - why: confirm default deterministic path and optional planner path remain runnable.
  - validation: `python3 -m pytest -q`, `python3 -m ruff check .`, `python3 -m black --check .`, and `cd apps/web && npm run build` passed.
  - next: operator can manually revalidate baseline mode and noop planner mode.
- 2026-04-27 / session:
  - changes: hardened OpenAI-compatible provider URL construction for DeepSeek base URLs, made planner JSON extraction tolerant of code fences and explanatory text, defaulted missing plan fields, and added `scripts/smoke_deepseek_planner.py`.
  - why: enable live DeepSeek planner validation while preserving the deterministic pipeline and API-key safety constraints.
  - validation: targeted provider/planner/pipeline tests passed; smoke script missing-key path exited `2`; `python3 -m pytest -q`, `python3 -m ruff check .`, `python3 -m black --check .`, and `cd apps/web && npm run build` passed.
  - next: operator can run `python scripts/smoke_deepseek_planner.py` with a real key in `.env`.
- 2026-04-28 / session:
  - changes: added planner post-processing guardrails for SearXNG definition/how-it-works queries, query-source metadata, source-selection intent metadata and priority guardrails, diagram/config chunk ineligibility, and figure/diagram/config claim rejection.
  - why: DeepSeek planner output should improve search coverage without allowing avoid-domain mistakes or architecture/admin pages to displace official about and Wikipedia reference sources.
  - validation: targeted planner, search discovery, acquisition, parsing quality, claim drafting, and pipeline API regression tests passed.
  - next: run full backend and frontend validation.

## 8. Validation

- `python3 -m pytest -q` - passed
- `python3 -m ruff check .` - passed
- `python3 -m black --check .` - passed
- `cd apps/web && npm run build` - passed
- targeted 2026-04-28 guardrail regression suite - passed

## 9. Risks and unknowns

- OpenAI-compatible APIs vary; v1 will target the stable chat completions shape because it is easy to mock and broadly compatible.
- Planner query expansion can increase search volume; v1 will cap and dedupe planner search queries using existing search limits.
- Historical tasks will not have planner events; UI must tolerate absence.
- Guardrails are intentionally rule-based and scoped to definition/overview and SearXNG software-project queries; unrelated domains may need future source-intent rules if they show similar planner drift.

## 10. Rollback / recovery

- Revert this plan plus touched LLM/planning/settings/pipeline/API/UI/docs/tests files.
- No migration rollback is needed because no schema migration is introduced.
- Disable with `RESEARCH_PLANNER_ENABLED=false` and `LLM_ENABLED=false`.

## 11. Deferred work

- Persisted `research_plan` table and richer plan versioning.
- Multi-step agent loop, planner-driven gap analysis, and iterative replanning.
- LLM-assisted claim synthesis or report writing.
