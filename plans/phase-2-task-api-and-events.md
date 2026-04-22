# Phase 2 Thin Research Task API And Event Stream

## 1. Objective

Implement the first public research task API surface for creation, lookup, event retrieval, and the minimal pause, resume, cancel, and revise state transitions, backed only by the existing database ledger and thin repositories.

## 2. Why this exists

Phase 2 turns the repository and schema groundwork from Phase 1 into an operator-visible task object. The platform needs a stable API and auditable event stream before any background execution, search, or evidence acquisition logic is introduced.

## 3. Scope

### In scope

- add the thin research task API endpoints:
  - `POST /api/v1/research/tasks`
  - `GET /api/v1/research/tasks/{task_id}`
  - `GET /api/v1/research/tasks/{task_id}/events`
  - `POST /api/v1/research/tasks/{task_id}/pause`
  - `POST /api/v1/research/tasks/{task_id}/resume`
  - `POST /api/v1/research/tasks/{task_id}/cancel`
  - `POST /api/v1/research/tasks/{task_id}/revise`
- add orchestrator-side service helpers that use the current repositories
- persist a `task_event` row for every task creation and status-changing action
- keep the Phase 2 status transitions limited and explicit
- add repository, service, and API tests
- update `docs/api.md`, `docs/runbook.md`, `docs/phases/phase-2.md`, and the active plan

### Out of scope

- worker processes, job queues, or task scheduling
- LangGraph execution, checkpoints beyond stored JSON fields, or runtime graph orchestration
- search, fetch, parse, index, verification, or reporting behavior
- new infrastructure dependencies
- report and claims HTTP endpoints

## 4. Constraints

- stay strictly within Phase 2
- do not add search, fetch, index, claim, or report behavior
- use only the existing database layer plus thin API/service wiring
- keep the Phase 2 active state subset minimal and explicit
- every status change must emit a stable `task_event`

## 5. Relevant files and systems

- `services/orchestrator/app/main.py`
- `services/orchestrator/app/settings.py`
- `services/orchestrator/app/api/`
- `services/orchestrator/app/services/`
- `packages/db/repositories/research.py`
- `packages/db/session.py`
- `conftest.py`
- `services/orchestrator/tests/`
- `tests/unit/`
- `docs/api.md`
- `docs/runbook.md`
- `docs/phases/phase-2.md`

## 6. Milestones

### Milestone 1
- intent: define the minimal task transition rules and event contract for Phase 2
- code changes: add the Phase 2 plan, route/service scaffolding, and shared constants for event types and transitions
- validation: unit-test valid and invalid transitions

### Milestone 2
- intent: expose the thin task API without adding execution semantics
- code changes: add database dependencies, request/response schemas, and task routes
- validation: API tests for create, get, list events, pause, resume, cancel, and revise

### Milestone 3
- intent: keep the persistence layer aligned with the API behavior
- code changes: extend repositories only where required for task lookups and event persistence
- validation: repository tests for state updates and event ordering

### Milestone 4
- intent: document the Phase 2 API and operator workflow
- code changes: update the API and runbook docs and add `docs/phases/phase-2.md`
- validation: manual comparison between docs and implemented endpoints

## 7. Implementation log

- 2026-04-22 session start: reread the repository instructions, product spec, current docs, and Phase 1 persistence layer. Constrained Phase 2 to a thin task API backed only by database state transitions and task events. Plan created.
- 2026-04-22 implementation: added orchestrator database session wiring, request/response schemas, and a `ResearchTaskService` that owns the minimal Phase 2 transition rules plus stable `task_event` payloads.
- 2026-04-22 implementation: added thin task routes for create, get, events, pause, resume, cancel, and revise, with `404` for missing tasks and `409` for invalid state transitions.
- 2026-04-22 implementation: extended the research repositories only where needed for Phase 2 persistence, including explicit task status updates, revision persistence, and deterministic event timestamps for SQLite-backed test ordering.
- 2026-04-22 implementation: added repository, service, and API tests and updated the API, schema, runbook, architecture, and phase docs.

## 8. Validation

- completed:
  - `python3 -m ruff check .`
  - `python3 -m black --check .`
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit`
  - `python3 -m pytest`
  - `rm -f /tmp/deepresearch_phase2.db && DATABASE_URL=sqlite:////tmp/deepresearch_phase2.db python3 -m alembic -c alembic.ini upgrade head`
  - `DATABASE_URL=sqlite:////tmp/deepresearch_phase2.db python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000`
  - `curl -fsS http://127.0.0.1:8000/healthz`
  - `curl -fsS http://127.0.0.1:8000/readyz`
  - `curl -fsS -X POST http://127.0.0.1:8000/api/v1/research/tasks ...`
  - `curl -fsS -X POST http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/pause`
  - `curl -fsS -X POST http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/resume`
  - `curl -fsS -X POST http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/revise ...`
  - `curl -fsS -X POST http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/cancel`
  - `curl -fsS http://127.0.0.1:8000/api/v1/research/tasks/<task_id>`
  - `curl -fsS http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/events`

- still unvalidated in this environment:
  - PostgreSQL-specific runtime behavior because the current environment has no PostgreSQL service
  - Docker-based API validation because `docker` is unavailable in the current environment

## 9. Risks and unknowns

- Phase 2 intentionally uses only the status subset needed for create, pause, resume, cancel, and revise; later phases will need to broaden active runtime states without breaking the API contract
- `revise` semantics are deliberately minimal here and only update persisted task fields plus a task event
- the API remains synchronous over database updates only; no background execution begins after `resume` or `revise`

## 10. Rollback / recovery

- revert the Phase 2 orchestrator route and service files
- if necessary, roll back repository helper changes that were introduced only for the task API
- no schema migration rollback is expected unless implementation uncovers a genuine Phase 2 schema need

## 11. Deferred work

- worker-triggered state transitions beyond the thin Phase 2 subset
- report and claims APIs
- execution-triggered `research_run` creation and checkpoint behavior
- search, fetch, parse, index, verify, and report runtime implementations
