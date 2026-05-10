# Quality Filtering, Time Display, and Observability Stability Fixes

## 1. Objective

Reduce non-fatal deterministic claim-candidate rejection, make UI-facing timestamps display in
China time, and keep research-depth observability stable after report generation.

## 2. Why this exists

A Gemini recent-news run showed `82` evidence candidates, `5` accepted candidates, and `74`
rejected candidates during `DRAFTING_CLAIMS`. Most rejections were deterministic scoring rules
such as `insufficient_answer_score`, `not_answer_relevant`, and `insufficient_claim_quality`,
which should rank candidates lower rather than remove them before optional LLM review. The same
run also showed task/report timestamps with the correct minutes and seconds but UTC hours in the
web UI, and the research-depth panel changed after `REPORTING` because task-detail observability
replaced full drafting-stage metrics with report-manifest-only metrics.

## 3. Scope

### In scope

- Keep hard claim rejection limited to fatal garbage, unsafe/ineligible chunks, and clearly
  non-claimable text.
- Preserve non-fatal low-score candidates for ranking and optional LLM claim review.
- Keep task-detail evidence/source yield metrics from the research pipeline stable after
  `REPORTING`.
- Keep fetch-success observability distinct from parsed source-document counts.
- Keep failed-response snapshots from consuming default parse-stage budget.
- Format web UI datetimes as China time and treat timezone-less API timestamps as UTC.
- Add focused tests for claim rejection, task observability aggregation, and datetime formatting.
- Update relevant docs and this plan log.

### Out of scope

- New database schema or migrations.
- Letting an LLM create claims, source rows, evidence rows, or reports without persisted evidence.
- Changing SSRF, MIME, duplicate URL, fetch-budget, or storage guardrails.
- Rewriting the full claim verifier, source judge, or report renderer.
- Docker-first deployment work.

## 4. Constraints

- Preserve `research_task` and ledger-first semantics.
- Do not weaken safety boundaries for URL acquisition, parsing, or object storage.
- Do not promote weak/unsupported evidence into final report conclusions.
- No new production dependencies.
- Work with the existing dirty worktree without reverting unrelated edits.

## 5. Relevant files and systems

- `services/orchestrator/app/services/claims.py`
- `services/orchestrator/app/api/routes/research_tasks.py`
- `services/orchestrator/app/research_quality/evidence.py`
- `services/orchestrator/app/services/parsing.py`
- `apps/web/src/pages/tasks/TaskDetailPage.tsx`
- `apps/web/src/pages/tasks/TaskListPage.tsx`
- `apps/web/src/pages/tasks/TaskReportPage.tsx`
- `apps/web/src/pages/tasks/TaskSourcesPage.tsx`
- `apps/web/src/lib/datetime.ts`
- `docs/api.md`
- `docs/runbook.md`

## 6. Milestones

### Milestone 1
- intent: Confirm the runtime failure modes from task events and local code.
- code changes: none.
- validation: Inspect task detail/events for the Gemini task and grep the relevant aggregation and
  rejection paths.

### Milestone 2
- intent: Relax deterministic candidate rejection without weakening safety.
- code changes: adjust `_strict_rejected_rules` and diagnostics so non-fatal score thresholds no
  longer hard-reject candidates.
- validation: focused claim helper/service tests.

### Milestone 3
- intent: Stabilize research-depth observability after report generation.
- code changes: prevent `REPORTING` event result summaries from overwriting full pipeline
  evidence/source yield metrics in task detail.
- validation: task-detail aggregation unit test or existing API/debug pipeline tests.

### Milestone 4
- intent: Fix China-time display in the web workspace.
- code changes: add shared datetime formatter and use it on task detail/list/report/sources event
  timestamps.
- validation: TypeScript build.

### Milestone 5
- intent: Document and close.
- code changes: update docs and implementation log.
- validation: run focused pytest, ruff, and frontend build checks.

### Milestone 6
- intent: Explain and reduce low apparent fetch-success counts.
- code changes: make default parsing skip failed-fetch snapshots before applying its limit, and
  make Task Detail's `获取成功` metric use fetch success rather than parsed source-document count.
- validation: parsing-service regression test and frontend build.

## 7. Implementation log

- 2026-05-08 / investigation:
  - Read the required repository governance docs. All required docs are present.
  - Inspected live task `b1be9dec-b20d-41aa-a086-e97994cc61e7`.
  - Confirmed drafting-stage evidence yield was `82 total / 5 accepted / 74 rejected`; top
    deterministic rejection reasons were non-fatal score/answer filters.
  - Confirmed reporting-stage manifest yield was `5 total / 5 accepted / 0 rejected`, and task
    detail currently uses that after completion, causing the UI jump.
  - Confirmed Markdown report body already renders Beijing time, while API timestamps are
    timezone-less UTC strings that the frontend interprets as local time.
  - Checked running backend/worker process environment. Proxy variables are present in the process
    environment, but `ACQUISITION_TRUST_ENV_PROXY=false` and `LLM_TRUST_ENV_PROXY=false`; no `TZ`
    variable is set. The displayed hour offset is from frontend parsing timezone-less UTC API
    timestamps, not from proxy routing.
- 2026-05-08 / implementation:
  - Relaxed deterministic claim-candidate hard rejection so low claim-quality, answer-score, and
    answer-relevance scoring stay as ranking/selection inputs instead of fatal rejection reasons.
  - Added `unselected_candidates` to evidence-yield summaries and updated task-detail aggregation
    to normalize old score-only rejection reasons as unselected compatibility data.
  - Stopped `REPORTING` stage report-manifest yield summaries from overwriting full pipeline
    source/evidence yield in task-detail observability.
  - Added shared web datetime helpers that treat timezone-less API timestamps as UTC and render
    task/report/source/event times in `Asia/Shanghai`.
  - Enabled the persisted-claim LLM reviewer by default in settings and host-local env examples,
    while keeping provider/proxy trust independently configurable.
  - Updated docs for hard-reject vs unselected semantics, LLM claim review, China-time display
    implications, and no-migration compatibility.
  - Rechecked the live Gemini task after reload: task detail now reports `82 total / 5 accepted /
    33 hard rejected / 44 unselected`, and the report body contains `2026-05-08 21:23:16
    (北京时间)`.
- 2026-05-08 / fetch-success diagnosis:
  - Inspected task `a4bb6616-f0ea-4a94-a165-13e0a2c1fc89`; it had 20 fetch attempts, 2 HTTP
    successes, 18 failed attempts, 14 stored snapshots, and only 1 parsed source document.
  - Found the main failure mix: 11 HTTP 403 responses from `openai.com` / `help.openai.com`,
    5 network errors, 1 oversized PDF, 1 HTTP 462, and one fetched TechCrunch snapshot that was
    never parsed.
  - Confirmed proxy variables exist in the process environment, but acquisition currently uses
    `ACQUISITION_TRUST_ENV_PROXY=false`; enabling environment proxy naively would fail in this
    environment because `ALL_PROXY` is SOCKS and the installed httpx lacks SOCKS support.
  - Fixed default parsing so failed fetch snapshots saved for audit do not consume parse budget
    ahead of successful 200 snapshots.
  - Fixed the Task Detail `获取成功` card to show `observability.fetch_succeeded` instead of
    `source_documents`.

## 8. Validation

- `python3 -m pytest tests/unit/orchestrator/test_llm_settings_and_providers.py tests/unit/orchestrator/test_task_observability.py tests/unit/orchestrator/test_evidence_quality.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py -q` passed.
- `python3 -m pytest services/orchestrator/tests/test_debug_pipeline_api.py::test_gap_search_provider_failure_continues_to_reporting_with_existing_evidence services/orchestrator/tests/test_health.py tests/unit/orchestrator/test_report_synthesis_service.py -q` passed.
- `python3 -m ruff check services/orchestrator/app/settings.py services/orchestrator/app/services/claims.py services/orchestrator/app/api/routes/research_tasks.py services/orchestrator/app/research_quality/evidence.py tests/unit/orchestrator/test_evidence_quality.py tests/unit/orchestrator/test_task_observability.py tests/unit/orchestrator/test_llm_settings_and_providers.py` passed.
- `python3 -m black --check services/orchestrator/app/settings.py services/orchestrator/app/services/claims.py services/orchestrator/app/api/routes/research_tasks.py services/orchestrator/app/research_quality/evidence.py tests/unit/orchestrator/test_evidence_quality.py tests/unit/orchestrator/test_task_observability.py tests/unit/orchestrator/test_llm_settings_and_providers.py` passed.
- `bash -n scripts/run_full_deepsearch.sh` passed.
- `npm run build` in `apps/web` passed.
- `npm run lint` in `apps/web` did not run because `eslint` is not installed in that package.
- `python3 -m pytest tests/unit/orchestrator/test_parsing_service.py -q` passed after the
  fetch-success diagnosis fix.
- `python3 -m pytest tests/unit/orchestrator/test_parsing_service.py tests/unit/orchestrator/test_task_observability.py -q` passed.
- `python3 -m ruff check services/orchestrator/app/services/parsing.py tests/unit/orchestrator/test_parsing_service.py` passed.
- `python3 -m black --check services/orchestrator/app/services/parsing.py tests/unit/orchestrator/test_parsing_service.py` passed.
- `npm run build` in `apps/web` passed again after the fetch-success card fix.

## 9. Risks and unknowns

- Existing task events keep their old drafting diagnostics; the fix affects newly generated task
  runs and how task detail aggregates reporting-stage metrics.
- Letting more candidates survive deterministic filters can increase verification input diversity,
  but final report safety still depends on verification and report eligibility gates.
- Timezone-less historical API timestamps remain stored as-is; the frontend compatibility formatter
  treats them as UTC for display.

## 10. Rollback / recovery

- Revert this plan and the scoped edits listed in the relevant files.
- No schema migration is involved.
- Existing persisted task/event/report rows remain readable.

## 11. Deferred work

- A richer LLM candidate-review stage over all candidate evidence spans, not only persisted draft
  claims.
- API-level datetime serialization with explicit timezone offsets across every endpoint.
- Separate UI cards for full-pipeline evidence yield versus report-included evidence yield.
