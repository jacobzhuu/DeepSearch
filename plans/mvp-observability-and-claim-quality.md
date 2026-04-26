# MVP Observability And Claim Quality Hardening

## 1. Objective

Harden the completed synchronous MVP loop by making real-search failures observable, exposing fetch/search outcomes in task events and task detail, adding basic Markdown report controls in the web UI, and filtering low-quality claim and citation material before it reaches reports.

## 2. Why this exists

The first real MVP test completed end to end, but it exposed operational gaps: weak search endpoint diagnostics, insufficient per-URL fetch failure visibility, a report page that hides raw Markdown, and deterministic claim drafting that can preserve short title-like or meaningless text as evidence-backed claims.

## 3. Scope

### In scope

- SearXNG response validation for HTML, 403, invalid JSON, and empty results with unresponsive engines.
- Structured SearXNG logs with provider, base URL, status, content type, body preview, and unresponsive engines.
- Pipeline stage-completion payloads with search result counts, selected source URLs, fetch success/failure counts, failed fetch URL details, and non-blocking low-source warnings.
- Task-detail progress observability derived from pipeline events.
- Report page HTML/Raw Markdown toggle, Copy Markdown, and Download `.md`.
- Deterministic claim and report filtering for short excerpts, title/query-like claims, incomplete sentence fragments, and case/punctuation duplicates.
- Focused unit/API tests plus full `python3 -m pytest -q`.

### Out of scope

- No LLM-backed drafting or verification.
- No worker, queue, LangGraph runner, or background execution.
- No browser fetch fallback, PDF/Tika parsing, HTML/PDF report export, or new production dependencies.
- No database schema migration.

## 4. Constraints

- Preserve Phase 11 host-local / self-hosted route.
- Keep the main product model centered on `research_task`.
- Keep failures ledger-observable through existing task events and fetch attempt records.
- Do not block MVP completion when one source succeeds; only warn when fewer than two sources succeed.
- Preserve the existing Markdown-only report artifact API.
- Keep all filtering deterministic and explainable.

## 5. Relevant files and systems

- `services/orchestrator/app/search/providers.py`
- `services/orchestrator/app/services/search_discovery.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/services/claims.py`
- `services/orchestrator/app/claims/drafting.py`
- `services/orchestrator/app/claims/verification.py`
- `services/orchestrator/app/services/reporting.py`
- `services/orchestrator/app/api/routes/research_tasks.py`
- `services/orchestrator/app/api/schemas/research_tasks.py`
- `apps/web/src/pages/tasks/TaskReportPage.tsx`
- `apps/web/src/pages/tasks/TaskDetailPage.tsx`
- `docs/api.md`
- `docs/runbook.md`

## 6. Milestones

### Milestone 1
- intent: make search and fetch outcomes inspectable
- code changes: SearXNG validator/logging, route error details, pipeline stage payloads, task progress observability
- validation: search-provider tests and pipeline event assertions

### Milestone 2
- intent: keep bad claim/evidence text out of claims and reports
- code changes: deterministic claimability helpers, draft dedupe, verification short-excerpt skip, report preparation filters
- validation: helper, service, and report synthesis tests

### Milestone 3
- intent: improve report page operator ergonomics without changing report artifacts
- code changes: HTML/raw toggle, copy button, download button
- validation: TypeScript build where available

### Milestone 4
- intent: update operator docs and validate the repository
- code changes: API/runbook docs and plan log updates
- validation: `python3 -m pytest -q`

## 7. Implementation log

- 2026-04-26 / session:
  - changes: plan created before implementation.
  - rationale: this task spans search, pipeline events, claim/report filtering, frontend UI, tests, and docs.
  - validation: pending.
  - next: implement milestone 1.
- 2026-04-26 / session:
  - changes: implemented SearXNG endpoint validation/logging, pipeline search/fetch observability payloads, task-detail observability aggregation, claim/report quality filters, report page Raw Markdown/Copy/Download controls, focused tests, and docs.
  - rationale: preserve the current no-worker/no-LLM MVP loop while improving operator diagnosis and report evidence quality.
  - validation: `python3 -m pytest -q` passed; `python3 -m ruff check .` passed; `python3 -m black --check .` passed; `npm run build` in `apps/web` passed.
  - next: run a real host-local task again against the operator's SearXNG endpoint and inspect warnings/failed sources in task detail.

## 8. Validation

- `python3 -m pytest -q` - passed
- focused search client tests - passed as part of full pytest
- focused claim/report filtering tests - passed as part of full pytest
- `python3 -m ruff check .` - passed
- `python3 -m black --check .` - passed
- `npm run build` in `apps/web` - passed

## 9. Risks and unknowns

- Some valid pages may still produce only one source because external sites return 403/CAPTCHA; this change surfaces that condition without hiding it or blocking completion.
- Deterministic claim filtering is intentionally conservative and may skip terse but valid factual snippets until later richer drafting exists.
- Existing historical bad claims remain in the ledger, but regenerated reports should no longer treat bad claims or short excerpts as supported evidence.

## 10. Rollback / recovery

- Revert this plan and the touched code/docs/tests.
- No migration rollback is needed because no schema changes are introduced.
- Existing report artifacts remain immutable; regenerate reports to apply filtering to current ledger state.

## 11. Deferred work

- Stronger source selection policy.
- Browser fallback for access-denied pages.
- Richer semantic claim deduplication.
- More detailed UI rendering for fetch attempts beyond event-derived summaries.
