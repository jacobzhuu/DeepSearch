# Phase 2 Runtime Readiness Contracts

## 1. Objective

Harden the thin research task API and ledger for the next phase by introducing stable per-task event ordering, task revision numbering, and documented future runtime-state placeholders without adding any real execution behavior.

## 2. Why this exists

Phase 2 currently proves only the minimal task lifecycle shell. Before worker-driven execution is introduced, the task ledger needs a stable ordering mechanism and a stable revision identifier so future runs, query snapshots, and report provenance can evolve without ambiguous event history or task mutations.

## 3. Scope

### In scope

- add the smallest schema support for:
  - `task_event.sequence_no`
  - `research_task.revision_no`
- reserve the future runtime-facing task statuses needed for later phases, including `QUEUED` and `RUNNING`
- keep `resume` semantics explicitly limited to returning a task to the current executable-candidate status
- extend repositories and services only where needed for:
  - deterministic event ordering
  - revision increments on `revise`
  - filtered event reads
- extend `GET /api/v1/research/tasks/{task_id}/events` with minimal polling-friendly parameters if they can remain backward compatible
- add repository, service, migration, and API tests
- update docs and the active plan

### Out of scope

- worker processes, job scheduling, or queue consumption
- LangGraph execution or checkpoint orchestration
- automatic transition into `QUEUED` or `RUNNING`
- search, fetch, parse, index, claim, or report behavior
- SSE, WebSocket, or message-bus event streaming

## 4. Constraints

- remain strictly outside real execution semantics
- keep existing event payload compatibility: `event_version`, `source`, `from_status`, `to_status`, `changes`
- keep existing routes backward compatible
- do not introduce new infrastructure dependencies
- prefer additive schema and API changes only
- migrations must remain reversible

## 5. Relevant files and systems

- `migrations/versions/`
- `packages/db/models/constants.py`
- `packages/db/models/ledger.py`
- `packages/db/repositories/research.py`
- `services/orchestrator/app/services/research_tasks.py`
- `services/orchestrator/app/api/routes/research_tasks.py`
- `services/orchestrator/app/api/schemas/research_tasks.py`
- `tests/unit/db/`
- `tests/unit/orchestrator/`
- `services/orchestrator/tests/`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/phases/phase-2.md`

## 6. Milestones

### Milestone 1
- intent: add the minimum schema needed for deterministic ordering and revision tracking
- code changes: new migration, ORM updates, and migration tests
- validation: Alembic upgrade and downgrade, schema assertions

### Milestone 2
- intent: keep repository and service behavior aligned with the new ledger fields
- code changes: event sequencing, revision increments, future runtime-state constants, and filtered event reads
- validation: repository and service unit tests

### Milestone 3
- intent: expose the new additive API contract without changing current execution semantics
- code changes: `/events` query parameters, event response fields, and detail or mutation response additions only where needed
- validation: API tests and manual task-event polling checks

### Milestone 4
- intent: document the contract clearly for the next phase
- code changes: update API, schema, runbook, and phase docs
- validation: manual doc-to-code comparison

## 7. Implementation log

- 2026-04-23 research: reread the repository instructions, current Phase 2 code, tests, and docs. Narrowed the change to event ordering, revision numbering, and future runtime-state placeholders only. New ExecPlan created.
- 2026-04-23 implementation: added a reversible migration for `research_task.revision_no`, `research_task.last_event_sequence_no`, and `task_event.sequence_no`, plus reserved `QUEUED` and `RUNNING` in the schema state checks.
- 2026-04-23 implementation: updated repositories and the task service so event writes allocate per-task sequence numbers, `revise` increments `revision_no`, and `resume` remains a return to the current executable-candidate status only.
- 2026-04-23 implementation: extended `GET /events` with additive `after_sequence_no` and `limit` parameters, added `revision_no` and `sequence_no` to responses, and updated repository, service, migration, and API tests.
- 2026-04-23 implementation: updated the API, schema, runbook, architecture, and phase docs to keep the contract explicit and phase-safe.
- 2026-04-23 validation: lint, format check, type check, full test suite, Alembic upgrade and downgrade, and a manual API polling flow all passed on a temporary SQLite database.

## 8. Validation

- completed:
  - `python3 -m ruff check .`
  - `python3 -m black --check .`
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit`
  - `python3 -m pytest`
  - `rm -f /tmp/deepresearch_phase2_runtime_readiness.db && DATABASE_URL=sqlite:////tmp/deepresearch_phase2_runtime_readiness.db python3 -m alembic -c alembic.ini upgrade head`
  - `DATABASE_URL=sqlite:////tmp/deepresearch_phase2_runtime_readiness.db python3 -m alembic -c alembic.ini downgrade base`
  - `DATABASE_URL=sqlite:////tmp/deepresearch_phase2_runtime_readiness.db python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000`
  - `curl -fsS -X POST http://127.0.0.1:8000/api/v1/research/tasks ...`
  - `curl -fsS -X POST http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/pause`
  - `curl -fsS -X POST http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/resume`
  - `curl -fsS -X POST http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/revise ...`
  - `curl -fsS http://127.0.0.1:8000/api/v1/research/tasks/<task_id>`
  - `curl -fsS http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/events`
  - `curl -fsS "http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/events?after_sequence_no=2&limit=2"`

- still unvalidated in this environment:
  - PostgreSQL runtime behavior because the current environment does not provide PostgreSQL
  - Docker-based validation because `docker` is unavailable in the current environment

## 9. Risks and unknowns

- the current compare-and-set event sequence allocator should be reviewed again once multiple worker writers exist for the same task
- introducing reserved runtime statuses must not accidentally imply they are user-writable today
- additive response fields are expected to remain backward compatible, but downstream clients that assume exact field sets may still need notice

## 10. Rollback / recovery

- downgrade the new migration to return to the Phase 2 schema
- revert repository, service, route, schema, and test changes tied to event sequencing and revision numbering
- restore the prior documentation if the contract is rolled back

## 11. Deferred work

- actual transitions into `QUEUED`, `RUNNING`, `FAILED`, `COMPLETED`, and `NEEDS_REVISION`
- run creation and query snapshot persistence
- push-based event delivery
- worker-coordinated task sequencing and stronger concurrency guards
