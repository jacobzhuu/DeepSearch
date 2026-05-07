# SEARCHING Stage Partial Failure Handling

## 1. Objective

Fix the live worker SEARCHING-stage control flow so partial search success is finalized safely:
persisted usable candidate URLs allow the pipeline to advance to acquisition, while no-candidate
search exhaustion fails terminally with diagnostics.

## 2. Why this exists

The LangGraph live acceptance task exposed a SEARCHING-stage stall after a fallback planner run:
some candidate URLs existed, a later SearXNG query failed with unresponsive engines, known-path
fallback added no new URLs because they were already present, and the task did not advance.

## 3. Scope

### In scope

- Search discovery handling for partial provider failure after candidates already exist.
- SEARCHING-stage candidate finalization using persisted candidate URLs.
- Diagnostic payloads for tolerated search provider failures.
- Regression tests for partial-success plus later provider failure / zero-new known-path fallback.
- Runbook note for the live acceptance timeout alias if needed.

### Out of scope

- Database schema changes.
- Environment file changes.
- Frontend changes.
- Search source broadening.
- Claim drafting, verification, source selection quality, or report generation changes.

## 4. Constraints

- No database migrations.
- Existing deployment acceptance checks remain intact.
- Preserve existing canonicalization and allow/deny filtering.
- Fail terminally when no usable candidates exist.
- Do not hardcode credentials or external service behavior.

## 5. Relevant files and systems

- `services/orchestrator/app/services/search_discovery.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `tests/unit/orchestrator/test_search_discovery_service.py`
- `tests/unit/orchestrator/test_pipeline_worker.py`
- `scripts/live_acceptance.py`
- `docs/runbook.md`

## 6. Milestones

### Milestone 1
- intent: identify and patch candidate finalization behavior in search discovery.
- code changes: tolerate provider failure only when candidate URLs are already persisted; return
  available candidates for downstream SEARCHING-stage summaries.
- validation: focused search discovery unit tests.

### Milestone 2
- intent: prove the pipeline leaves SEARCHING in the exact regression scenario.
- code changes: add worker/pipeline regression with first-query candidates, later provider failure,
  zero-new known-path fallback, then forced fetch failure after ACQUIRING starts.
- validation: focused pipeline unit test.

### Milestone 3
- intent: support the documented live acceptance command and operator guidance.
- code changes: add `--timeout-seconds` alias and runbook note if necessary.
- validation: CLI help and script compile checks.

## 7. Implementation log

- 2026-05-06: Plan created after LangGraph live acceptance exposed SEARCHING partial-failure hang.
  Next: implement narrow search discovery and regression test changes.
- 2026-05-06: Implemented persisted-candidate finalization in search discovery, added tolerated
  provider-failure diagnostics, updated SEARCHING observability with available candidate counts,
  added focused search-discovery and pipeline regression tests, added the live acceptance
  `--timeout-seconds` alias, and documented bounded SEARCHING partial-failure behavior. Validation
  passed, including live LangGraph acceptance.

## 8. Validation

- `python -m pytest tests/unit/orchestrator/test_search_discovery_service.py -q`
- `python -m pytest tests/unit/orchestrator/test_pipeline_worker.py -q`
- `python -m py_compile scripts/live_acceptance.py scripts/live_acceptance_framework.py`
- `python -m ruff check <touched python files>`
- `python -m black --check <touched python files>`
- Live acceptance command when backing services are available.

Completed validation:

- `python -m pytest tests/unit/orchestrator/test_search_discovery_service.py::test_discover_candidates_tolerates_later_langgraph_failure_when_candidates_exist tests/unit/orchestrator/test_pipeline_worker.py::test_pipeline_leaves_searching_when_later_search_failure_has_existing_candidates -q` — passed
- `python -m pytest tests/unit/orchestrator/test_search_discovery_service.py tests/unit/orchestrator/test_pipeline_worker.py tests/unit/test_live_acceptance_framework.py -q` — passed
- `python -m py_compile services/orchestrator/app/services/search_discovery.py services/orchestrator/app/services/debug_pipeline.py scripts/live_acceptance.py` — passed
- `python -m ruff check services/orchestrator/app/services/search_discovery.py services/orchestrator/app/services/debug_pipeline.py tests/unit/orchestrator/test_search_discovery_service.py tests/unit/orchestrator/test_pipeline_worker.py scripts/live_acceptance.py` — passed
- `python -m black --check services/orchestrator/app/services/search_discovery.py services/orchestrator/app/services/debug_pipeline.py tests/unit/orchestrator/test_search_discovery_service.py tests/unit/orchestrator/test_pipeline_worker.py scripts/live_acceptance.py` — passed
- `DEV_ENV_FILE=.env.deepseek.local DEV_SKIP_FRONTEND=true DEV_BACKEND_RELOAD=false ./dev.sh restart` — passed
- `python scripts/live_acceptance.py --profile langgraph-technical-explanation --base-url http://127.0.0.1:8000 --artifact-dir /tmp/deepsearch-live-langgraph-acceptance --json-output /tmp/deepsearch-live-langgraph-acceptance.json --timeout-seconds 600` — passed

## 9. Risks and unknowns

- External SearXNG/LLM behavior can still make the live acceptance fail for quality reasons; the
  fixed guarantee is that SEARCHING does not silently remain active.
- Existing dirty worktree changes in nearby pipeline and search-quality files must be preserved.

## 10. Rollback / recovery

Revert the touched script, service, test, doc, and plan files. No migration or data rollback is
required.

## 11. Deferred work

- No new source-quality tuning.
- No new retry scheduler or distributed worker lease behavior.
