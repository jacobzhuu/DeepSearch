# Frontend Triggered DeepSearch Loop

## 1. Objective

Add the smallest production-facing synchronous orchestration path that lets the web frontend create a `research_task`, run the existing DeepSearch service chain, and display persisted sources, claims, evidence, and a Markdown report.

## 2. Why this exists

The backend already has real service slices and a development-only debug pipeline, and the frontend can show stable empty states. The missing product increment is a UI-triggered end-to-end run surface with clear task status, progress events, and structured failure diagnostics.

## 3. Scope

### In scope

- Add a non-debug `POST /api/v1/research/tasks/{task_id}/run` endpoint.
- Reuse existing search, acquisition, parsing, indexing, claim, verification, and reporting services.
- Preserve existing Phase 2 task APIs.
- Add explicit development smoke search and local in-process index backend modes, marked as non-real search/index modes.
- Update frontend task creation/detail flows to run the pipeline and surface failure details.
- Update tests and runbook.

### Out of scope

- No worker, queue, Celery, Redis, LangGraph runner, or background execution.
- No LLM API client or key handling.
- No Tika, PDF, Office parsing, browser fallback, or report export formats.
- No database schema migration.

## 4. Constraints

- Do not hardcode API keys.
- Do not present smoke data as real search.
- Do not bypass existing service/repository layers.
- Keep host-local operation primary.
- Keep all important side effects ledger-backed and traceable.
- Preserve existing debug endpoint compatibility where practical.

## 5. Relevant files and systems

- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/api/routes/debug_pipeline.py`
- `services/orchestrator/app/api/routes/research_tasks.py`
- `services/orchestrator/app/search/providers.py`
- `services/orchestrator/app/indexing/backends.py`
- `apps/web/src/features/tasks/*`
- `apps/web/src/pages/tasks/*`
- `docs/api.md`
- `docs/runbook.md`

## 6. Milestones

### Milestone 1
- intent: make backend pipeline a product-facing run command with valid task states and structured failure
- code changes: run endpoint, pipeline response schema, task progress derivation, service allowed-status support
- validation: API tests for success and failure

### Milestone 2
- intent: support explicit development smoke mode when real SearXNG/OpenSearch are absent
- code changes: `SEARCH_PROVIDER=smoke`, `INDEX_BACKEND=local`
- validation: provider/backend unit tests and smoke endpoint test

### Milestone 3
- intent: expose one-click run in frontend
- code changes: task API/hook/types, new-task create-and-run, detail run button, event/failure/count display
- validation: `npm run build`

### Milestone 4
- intent: document and validate host-local path
- code changes: runbook/API docs, plan log
- validation: pytest, ruff, frontend build, local smoke run where possible

## 7. Implementation log

- 2026-04-24: Plan created before implementation. Current known blockers are wrong `SEARXNG_BASE_URL` and absent OpenSearch on `127.0.0.1:9200`; the intended local validation path is explicit `SEARCH_PROVIDER=smoke` plus `INDEX_BACKEND=local`.
- 2026-04-24: Added `POST /api/v1/research/tasks/{task_id}/run`, runtime status transitions, structured failure payloads with `next_action`, explicit `SEARCH_PROVIDER=smoke`, and `INDEX_BACKEND=local`. Updated frontend task creation/detail pages so the operator can create and run a task from the UI and inspect events/counts/failures.
- 2026-04-24: Validated a smoke/local run through a temporary backend on port `18000`. Generated task `0ccaad31-d135-4ac0-88db-93f2b0884f05` completed with one search query, candidate URL, fetch attempt, snapshot, source document, source chunk, indexed chunk, claim, evidence link, and report artifact.

## 8. Validation

Planned commands:

- `python3 -m ruff check ...`
- `python3 -m ruff check .` - passed
- `python3 -m black --check .` - passed after formatting `services/orchestrator/tests/test_debug_pipeline_api.py`
- `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit` - passed
- `python3 -m pytest ...` - passed for focused pipeline/search/index coverage
- `python3 -m pytest` - passed, 107 tests
- `npm run build` in `apps/web` - passed
- temporary smoke/local backend on `127.0.0.1:18000` with `SEARCH_PROVIDER=smoke INDEX_BACKEND=local` - passed API run
- temporary Vite dev server on `127.0.0.1:15173` pointed at the smoke/local backend - loaded successfully

Known unvalidated areas before implementation:

- Real SearXNG is not currently reachable at configured `127.0.0.1:8080`.
- Real OpenSearch is not currently reachable at configured `127.0.0.1:9200`.

## 9. Risks and unknowns

- Synchronous HTTP run can take longer than a browser request if real search/fetch targets are slow.
- Local in-process index backend is development-only and loses indexed documents on process restart.
- Smoke search is not real research evidence and must remain visibly marked.

## 10. Rollback / recovery

- Revert the frontend run-button changes and the new run route/schema.
- Keep existing Phase 2 task APIs and individual phase endpoints intact.
- No schema rollback is required because this work does not add migrations.

## 11. Deferred work

- Background worker and queue execution.
- Real LLM-backed claim drafting/verifier.
- Durable local index backend or OpenSearch-only production hardening.
- Real dependency readiness probes with deep checks.
