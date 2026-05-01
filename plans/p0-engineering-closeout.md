# P0 Engineering Closeout for Host-Local DeepSearch v1 Alpha

## 1. Objective

Bring the current host-local DeepSearch path to a v1 alpha engineering closeout by fixing type-check failures, verifying that product `/run` queues work for the host-local worker, adding basic frontend task operation controls, and correcting docs that overstate implemented behavior.

## 2. Why this exists

The repository already has a runnable research-task ledger and pipeline, but it is not ready to present as a self-hosted alpha while `mypy` fails, the web UI lacks basic task controls, and operator documentation is not fully aligned with the code. This plan keeps the closeout focused on correctness, traceability, and recoverability rather than adding new research features.

## 3. Scope

### In scope

- Fix current `mypy` errors across `packages/db`, `services/orchestrator/app`, `services/orchestrator/tests`, and `tests/unit`.
- Confirm product `/api/v1/research/tasks/{task_id}/run` queues tasks and the host-local worker reuses the same core pipeline runner.
- Add minimal frontend task list and Run / Pause / Resume / Cancel controls.
- Add or update host-local real-dependency smoke instructions or scripts.
- Update architecture, API, schema, and runbook docs to reflect current implementation boundaries.
- Run required lint, format, test, frontend build, and type checks.

### Out of scope

- New database schema or migrations.
- Distributed queues, worker leases, browser fetching, Tika/PDF/Office parsing, embeddings, reranking, or new production dependencies.
- Moving report generation wholesale to LLMs.
- Implementing LLM source judge, active LLM reranking, or LLM gap reasoner.

## 4. Constraints

- Preserve `research_task` as the product center.
- Keep the host-local Linux route primary; compose remains optional tooling.
- Do not mask type errors with broad `Any`, mass `ignore`, or large behavior-erasing casts.
- Keep debug pipeline as a development entry point while product `/run` remains worker-driven.
- Preserve ledger provenance and current state-machine semantics.
- Validate all changed code.

## 5. Relevant files and systems

- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/services/pipeline_worker.py`
- `services/orchestrator/app/services/pipeline_runtime.py`
- `services/orchestrator/app/services/research_tasks.py`
- `services/orchestrator/app/api/routes/research_tasks.py`
- `services/orchestrator/app/api/routes/pipeline.py`
- `apps/web/src/`
- `scripts/`
- `docs/architecture.md`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `packages/db/`
- `tests/unit/`
- `services/orchestrator/tests/`

## 6. Milestones

### Milestone 1
- intent: repair type-checking without hiding errors.
- code changes: targeted annotations, narrowed payload types, protocol/dataclass fixes, and test helper typing.
- validation: `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit`.

### Milestone 2
- intent: confirm `/run` and worker share one core pipeline path.
- code changes: only minimal refactor or tests if drift is found.
- validation: unit tests for worker/run path and code inspection of ledger writes.

### Milestone 3
- intent: expose basic task operations in the web workspace.
- code changes: task list page, API hooks, status-aware action buttons, recent event rendering.
- validation: frontend build and relevant tests.

### Milestone 4
- intent: make real-dependency smoke operation reproducible.
- code changes: smoke script or runbook steps for PostgreSQL, MinIO, OpenSearch, SearXNG, orchestrator, worker, and web.
- validation: script help or dry path, plus documented unvalidated external dependency areas.

### Milestone 5
- intent: remove docs/code drift.
- code changes: architecture, API, schema, and runbook corrections.
- validation: doc review plus full required command set.

## 7. Implementation log

- 2026-05-01: Plan created after P0 closeout request. Initial repo review shows existing unrelated frontend dist/log worktree changes. Next step is to reproduce and fix `mypy`.
- 2026-05-01: Fixed all `mypy` failures with typed ownership profiles, JSON metadata narrowing, smoke acquisition constructor typing, protocol-based worker runner typing, and explicit test helpers for nullable ledger JSON. `mypy` now passes without broad ignores.
- 2026-05-01: Added `GET /api/v1/research/tasks`, repository/service list support, and API coverage for recent task summaries. The endpoint is read-only and uses existing `research_task` plus `task_event` data.
- 2026-05-01: Confirmed product `/run` remains queue-only and host-local worker execution reuses the shared core pipeline runner. Added a worker-path test that queues through `/run`, consumes the queued task with `ResearchPipelineWorker`, and verifies `source_document`, `source_chunk`, `claim`, `claim_evidence`, and `report_artifact` ledger writes.
- 2026-05-01: Added the web task list page and status-aware Run/Pause/Resume/Cancel controls on task detail, reusing existing backend lifecycle endpoints.
- 2026-05-01: Updated `scripts/smoke_planner_pipeline.py` to validate the worker-path ledger closure rather than only a planner-heavy claim count, and updated architecture/API/schema/runbook docs for current worker/debug boundaries, parsing formats, source-quality limits, and pause/resume/cancel boundaries.

## 8. Validation

Required final commands:

- `python3 -m ruff check .` — passed
- `python3 -m black --check .` — passed
- `python3 -m pytest` — passed, `269 passed in 48.52s`
- `cd apps/web && npm run build` — passed
- `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit` — passed
- `python3 scripts/smoke_planner_pipeline.py --help` — passed, verifies the updated worker-smoke CLI loads

Live PostgreSQL + MinIO + OpenSearch + SearXNG validation was not run in this implementation session because those external services were not started as part of the closeout. The runbook now documents the exact host-local sequence and the smoke command to run once the stack is available.

## 9. Risks and unknowns

- Existing frontend `dist/` and log changes are already present and should not be reverted as part of this work.
- Real-dependency smoke may require services or credentials not available in the current shell.
- Fixing type errors may reveal latent contract ambiguity in loosely typed JSON event payloads.

## 10. Rollback / recovery

No schema changes are planned. Rollback is a normal git revert of this plan, targeted code changes, frontend additions, scripts, and docs. Runtime data created by any smoke run should be treated as disposable validation data.

## 11. Deferred work

- Distributed worker lease and heartbeat protocol.
- Browser, Tika, PDF, Office, and attachment parsing.
- LLM source judge, active reranking, and LLM gap reasoner.
- Embedding or hybrid retrieval.
- Authentication and multi-user controls.
