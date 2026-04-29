# ExecPlan: multi-round gap analyzer and host-local worker

## Goal

Implement the next minimal product increment for:

- P5: run supplemental research rounds when required answer slots are missing or weak.
- P6: make product `POST /run` enqueue a long-running `research_task` and let a host-local worker execute it with visible progress and checkpoints.

This plan keeps the repository centered on `research_task`, uses the existing ledger tables, and avoids adding Docker-first or distributed queue infrastructure.

## Scope

- Add a deterministic gap analyzer that inspects verified slot coverage and emits bounded supplemental search queries.
- Extend the existing pipeline runner to run `SEARCHING -> ACQUIRING -> PARSING -> INDEXING -> DRAFTING_CLAIMS -> VERIFYING` again for gap rounds before `REPORTING`.
- Record gap-analysis events, supplemental query metadata, and stage checkpoints in `research_run.checkpoint_json`.
- Add `QUEUED` task transition support for product runs.
- Add a host-local worker loop that polls queued tasks, recovers interrupted runtime statuses to `QUEUED`, and invokes the existing pipeline runner.
- Update the web workspace to treat `/run` as enqueue and poll task/events while active.
- Update API, architecture, schema, runbook, and active phase docs.

## Non-goals

- No Redis/Celery/external queue.
- No new database tables unless checkpoint semantics cannot be expressed safely in existing ledger tables.
- No frontend redesign beyond progress polling and queued/running labels.
- No LLM gap planner in this turn; supplemental queries are deterministic and evidence-first.

## Progress

- [x] Read required project docs and current implementation.
- [x] Add gap analyzer module and tests.
- [x] Extend pipeline runner for gap rounds, checkpoints, and pause/cancel checks.
- [x] Add enqueue service method and product `/run` async behavior.
- [x] Add host-local worker service/script.
- [x] Update frontend polling and async run UX.
- [x] Update docs.
- [x] Run targeted backend/frontend validation.
- [x] Run full backend unit/API validation.

## Validation

- `python3 -m pytest tests/unit/orchestrator/test_gap_analyzer.py tests/unit/orchestrator/test_pipeline_worker.py services/orchestrator/tests/test_debug_pipeline_api.py -q` — passed.
- `python3 -m pytest tests/unit/orchestrator/test_indexing_service.py::test_indexing_service_rejects_paused_task_and_blank_query -q` — passed; verifies downgrade-safe runtime-state normalization after `resume` queues work.
- `python3 -m pytest -q` — passed.
- `python3 -m ruff check migrations/versions/20260423_0002_task_revision_and_event_sequence.py services/orchestrator/app/research_quality/gap_analyzer.py services/orchestrator/app/planning/types.py services/orchestrator/app/services/search_discovery.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/api/routes/pipeline.py scripts/benchmark_queries.py tests/unit/orchestrator/test_gap_analyzer.py services/orchestrator/tests/test_debug_pipeline_api.py tests/unit/orchestrator/test_pipeline_worker.py` — passed.
- `python3 -m black --check migrations/versions/20260423_0002_task_revision_and_event_sequence.py services/orchestrator/app/research_quality/gap_analyzer.py services/orchestrator/app/planning/types.py services/orchestrator/app/services/search_discovery.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/api/routes/pipeline.py scripts/benchmark_queries.py tests/unit/orchestrator/test_gap_analyzer.py services/orchestrator/tests/test_debug_pipeline_api.py tests/unit/orchestrator/test_pipeline_worker.py` — passed.
- `npm run build` from `apps/web` — passed.
- `git diff --check` — passed.
- `npm run lint` from `apps/web` — failed because `eslint` is not installed or not available in the current frontend dependency set.
- `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit` — failed on existing type-check debt across parser, claim, acquisition, settings-test, and service test files; not introduced as a runtime blocker for this turn.

## Deferred

- Distributed queue and leases.
- Fine-grained mid-stage cancellation inside fetch/parse/index loops.
- LLM-generated supplemental search strategy.
- UI controls for pause/resume/cancel while a worker is running.
