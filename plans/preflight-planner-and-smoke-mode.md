# Preflight Planner and Smoke Mode Separation

## 1. Objective

Make the current web workflow distinguish development smoke validation from real research, add an explicit pre-run research-plan step that can be confirmed or edited by the operator, and fix deployment-oriented claim classification so Docker/deployment questions map to deployment answer slots.

## 2. Why this exists

Recent web tests completed too quickly and looked like product research even though the app was running the deterministic smoke path with `SEARCH_PROVIDER=smoke`, `INDEX_BACKEND=local`, and no LLM planner. The UI needs to make that mode unmistakable, and planner output should be visible before search starts so the operator can inspect or edit the planned subquestions and queries.

Deployment questions also produced supported claims that were categorized as `setup`, leaving required deployment slots marked missing even though evidence-backed deployment statements existed.

## 3. Scope

### In scope

- Expose runtime mode/dependency diagnostics in task detail observability.
- Add a narrow pre-run plan endpoint that records `research_plan.created` before `SEARCHING`.
- Let the pipeline reuse the latest pre-run `research_plan.created` event instead of generating a hidden duplicate plan.
- Add web UI flow: create task, generate plan, show/edit plan JSON, then start research.
- Add visible UI warnings for smoke/local/no-LLM mode.
- Fix deployment query intent/category/slot mapping.
- Update API/runbook/schema docs and focused tests.

### Out of scope

- New database tables or migrations.
- Background worker or queue semantics.
- Multi-round gap analyzer.
- LLM-written claims or final reports.
- Report language localization beyond preserving the existing deterministic report renderer.

## 4. Constraints

- Preserve `research_task` as the primary product object.
- Store planner output in existing `task_event.payload_json`; do not add a `research_plan` table.
- Planner output remains a bounded search-planning suggestion only.
- Claim/report facts must remain evidence-backed through citation spans.
- Smoke mode must remain available for deterministic operator validation, but must not look like product-quality research in the UI.
- No new production dependencies.

## 5. Relevant files and systems

- `services/orchestrator/app/planning/planner.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/services/research_tasks.py`
- `services/orchestrator/app/api/routes/research_tasks.py`
- `services/orchestrator/app/api/schemas/research_tasks.py`
- `services/orchestrator/app/claims/drafting.py`
- `services/orchestrator/app/research_quality/answer_slots.py`
- `apps/web/src/pages/tasks/NewTaskPage.tsx`
- `apps/web/src/pages/tasks/TaskDetailPage.tsx`
- `apps/web/src/features/tasks/*`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`

## 6. Milestones

### Milestone 1
- intent: establish a recoverable implementation plan.
- code changes: add this ExecPlan.
- validation: inspect plan content and keep implementation log current.

### Milestone 2
- intent: support a pre-run research-plan event without schema changes.
- code changes: add request/response schemas, route, service helper, planner payload coercion, and pipeline reuse of latest plan event.
- validation: focused backend tests and manual API create-plan-run check.

### Milestone 3
- intent: classify deployment evidence correctly.
- code changes: add deployment intent and prioritize deployment/self-hosting classification before setup instructions where the query asks for Docker/deploy/deployment.
- validation: unit tests for Docker deployment slot coverage and claim notes.

### Milestone 4
- intent: make smoke mode visually distinct and add plan confirmation in the web flow.
- code changes: frontend creates task first, generates plan, lets operator edit JSON, then runs; task detail shows runtime mode warnings.
- validation: web build and manual smoke UI/API check.

### Milestone 5
- intent: update operator documentation.
- code changes: document pre-run planner endpoint, smoke warning semantics, and no-schema planner storage.
- validation: docs reviewed for API/schema/runbook consistency.

## 7. Implementation log

- 2026-04-29: Created plan after web tests showed smoke-mode output being confused with product-quality Deep Research. Next step is backend pre-run planner support and deployment classification tests.
- 2026-04-29: Added pre-run `POST /api/v1/research/tasks/{task_id}/plan`, deterministic fallback planning, operator-edited plan confirmation, task-detail runtime mode observability, and pipeline reuse of current-revision plan events. Fixed Docker/deployment intent and slot mapping. Updated the web create flow to require plan review before running and to show smoke/local/no-LLM warnings. Next step is full validation and any cleanup found by tests.
- 2026-04-29: Tightened planner guardrails so deployment queries produce `deployment` intent, deployment-focused search queries, and deployment answer slots in deterministic fallback and in guarded LLM/noop planner output. Validated the local create-plan-run path after restarting services.
- 2026-04-29: Added a create-page recovery path for the case where task creation succeeds but plan generation fails, so the operator can retry plan generation or create another task without leaving the page.
- 2026-04-29: Follow-up P3/P4 work moved LLM-grounded report writing and report-language support out of this plan's deferred list into `plans/grounded-llm-report-language.md`.

## 8. Validation

- `pytest services/orchestrator/tests -q` or narrower affected tests.
- `cd apps/web && npm run build`.
- Manual API: create task, call plan endpoint, confirm task detail has `research_plan.created`, run task, verify planner is reused and `SEARCHING` follows the pre-run plan event.
- Manual API: Docker deployment query should mark deployment slots covered when supported deployment claims exist.

Current validation:

- `python -m compileall -q services/orchestrator/app/planning services/orchestrator/app/services services/orchestrator/app/api services/orchestrator/app/claims services/orchestrator/app/research_quality` — passed
- `pytest services/orchestrator/tests/test_deployment_claim_quality.py services/orchestrator/tests/test_research_tasks_api.py::test_plan_endpoint_records_visible_pre_run_research_plan services/orchestrator/tests/test_debug_pipeline_api.py::test_pipeline_reuses_pre_run_research_plan_when_planner_disabled -q` — passed
- `pytest services/orchestrator/tests/test_research_tasks_api.py services/orchestrator/tests/test_debug_pipeline_api.py services/orchestrator/tests/test_deployment_claim_quality.py services/orchestrator/tests/test_claims_api.py -q` — passed
- `git diff --check` — passed
- `cd apps/web && npm run build` — passed
- `./dev.sh restart` — passed; backend `http://127.0.0.1:8000`, frontend `http://127.0.0.1:5173`.
- Manual API create-plan-run for `How to deploy SearXNG with Docker?` — passed; `research_plan.created` precedes `pipeline.started`, plan intent is `deployment`, running mode is `smoke-search+deterministic-local+no-LLM`, and completed event counts show 2 claims and 1 report artifact.

## 9. Risks and unknowns

- Existing planner is optional and planner-only; with `LLM_ENABLED=false` the pre-run plan must fall back to deterministic/noop planning rather than implying LLM usage.
- Editing a plan through JSON is intentionally minimal; richer form editing is deferred.
- Existing report renderer remains English and deterministic; this plan does not add LLM report writing.

## 10. Rollback / recovery

- Revert the route/schema/frontend changes and remove this plan.
- Existing tasks remain readable because planner output is stored as ordinary `task_event` rows.
- No database rollback is required because no migration is introduced.

## 11. Deferred work

- Full async worker/queue execution.
- Multi-round gap analyzer.
- First-class persisted `research_plan` table.
- Rich plan editing UI beyond JSON.
