# Authoritative Source Discovery Hardening

## 1. Objective

Improve the free/self-hosted source-discovery path so technical framework, library, infrastructure,
and research-concept questions get authoritative candidates before noisy SearXNG fallback results.

## 2. Why this exists

The six-task live acceptance rerun failed primarily because SearXNG fallback engines returned
irrelevant specialty-engine results and raw provider timeouts could abort a run before useful
sources were persisted. The claim/report layers cannot recover when acquisition fetches mostly
off-entity pages.

## 3. Scope

### In scope

- Deterministic authoritative-source resolver for common technical source classes.
- Provider-result quality gates for off-entity specialty-engine results.
- Bounded SearXNG timeout/request-error diagnostics through `SearchProviderError`.
- Search diagnostics showing provider status, selection counts, rejected/noisy counts, and resolver injection.
- Unit/smoke/live validation.

### Out of scope

- Paid search providers.
- DeepSeek or any LLM as a search provider.
- Database migrations.
- Report prose, claim-review tuning, or unrelated UI styling.

## 4. Constraints

- Preserve the `research_task -> search_query -> candidate_url -> fetch_attempt -> content_snapshot -> source_document -> source_chunk -> claim/evidence/report` ledger.
- Use existing JSON diagnostic seams only.
- Keep smoke mode runnable without real search or LLM.
- Do not hard-code the exact six prompts as one-off cases.

## 5. Relevant files and systems

- `services/orchestrator/app/search/known_sources.py`
- `services/orchestrator/app/search/providers.py`
- `services/orchestrator/app/services/search_discovery.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/research_quality/source_intent.py`
- `tests/unit/orchestrator/test_search_discovery_service.py`
- `tests/unit/orchestrator/test_search_helpers.py`
- `docs/architecture.md`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`

## 6. Milestones

### Milestone 1
- intent: inspect failed artifacts and identify discovery failure modes.
- code changes: none.
- validation: summarize source domains, search failures, and counts from previous live artifacts.

### Milestone 2
- intent: inject authoritative candidates before SearXNG dependency.
- code changes: add deterministic resolver and persist resolver diagnostics through `search_query`/`candidate_url`.
- validation: unit tests for FastAPI/PyTorch/Kubernetes-style technical queries.

### Milestone 3
- intent: prevent noisy SearXNG specialty results from dominating acquisition.
- code changes: quality gates for off-entity MDN/StackOverflow/social/specialty results and diagnostics.
- validation: unit tests with noisy MDN results and authoritative candidates.

### Milestone 4
- intent: bound provider timeouts and supplemental failures.
- code changes: wrap SearXNG `httpx` timeouts/request errors as `SearchProviderError`; tolerate failures when candidates/evidence exist.
- validation: unit tests plus six-task live rerun.

## 7. Implementation log

- 2026-05-07 / initial implementation:
  - changed: added `known_sources.py`, authoritative resolver injection, provider result diagnostics, noisy specialty-engine rejection, SearXNG timeout wrapping, and source-intent ownership profiles for common technical projects/concepts.
  - why: move source discovery away from sole dependence on SearXNG general results without adding paid APIs or bypassing the ledger.
  - validation: targeted search-discovery and search-helper tests passed.
  - next: run smoke, lint/format, and six-task live acceptance.
- 2026-05-07 / comparison-source completion:
  - changed: comparison acquisition now interleaves authoritative candidates by known source entity,
    and source-intent ownership checks consider all extracted comparison entities instead of only the
    first one.
  - why: the first live rerun completed all six tasks but the LangGraph/AutoGen comparison fetched
    only LangGraph sources; this was still a source-selection failure.
  - validation: final six-task live acceptance completed all six tasks and fetched AutoGen
    `microsoft.github.io` alongside LangGraph docs for the comparison task. Artifacts:
    `/tmp/deepsearch-six-task-source-discovery-final-20260506T174514Z`.
  - next: no source-discovery implementation step remains in this turn.
- 2026-05-07 / pipeline regression stabilization:
  - changed: kept authoritative-source resolver available for standalone search discovery, but disabled
    it in the worker/debug pipeline `SEARCHING` loop; pipeline source recovery now relies on planner
    guardrail queries and known-path fallback so resolver candidates do not consume early acquisition
    slots or repeat during gap rounds. Gap slot coverage now applies the same report-facing answer
    relevance and query-focus checks before treating persisted claims as covering required slots.
  - why: unit regressions showed resolver rows could duplicate planner queries, crowd out provider
    candidates that would fetch successfully, reduce known-path fallback counts to duplicates, and
    hide report-visible coverage gaps from the gap analyzer.
  - validation: the focused debug-pipeline regressions and the user-provided unit/API test set passed.
  - next: optional live rerun to confirm the unit-level behavior under real SearXNG variability.

## 8. Validation

- `pytest tests/unit/orchestrator/test_search_discovery_service.py tests/unit/orchestrator/test_search_helpers.py -q` — passed.
- `python3 -m black --check services/orchestrator/app/search/known_sources.py services/orchestrator/app/search/providers.py services/orchestrator/app/services/search_discovery.py services/orchestrator/app/services/acquisition.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/research_quality/source_intent.py tests/unit/orchestrator/test_search_discovery_service.py tests/unit/orchestrator/test_search_helpers.py tests/unit/orchestrator/test_acquisition_service.py tests/unit/orchestrator/test_research_quality.py` — passed.
- `python3 -m ruff check services/orchestrator/app/search/known_sources.py services/orchestrator/app/search/providers.py services/orchestrator/app/services/search_discovery.py services/orchestrator/app/services/acquisition.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/research_quality/source_intent.py tests/unit/orchestrator/test_search_discovery_service.py tests/unit/orchestrator/test_search_helpers.py tests/unit/orchestrator/test_acquisition_service.py tests/unit/orchestrator/test_research_quality.py` — passed.
- `pytest tests/unit/orchestrator/test_search_discovery_service.py tests/unit/orchestrator/test_search_helpers.py tests/unit/orchestrator/test_acquisition_service.py tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_pipeline_worker.py services/orchestrator/tests/test_debug_pipeline_api.py::test_run_endpoint_queue_is_consumed_by_host_local_worker -q` — passed.
- `./dev.sh restart` — passed against host-local SQLite/OpenSearch/filesystem/SearXNG configuration.
- six-task live acceptance rerun against `http://127.0.0.1:8000` — all six completed; final artifact directory `/tmp/deepsearch-six-task-source-discovery-final-20260506T174514Z`.
- `pytest tests/unit/orchestrator/test_llm_assistance.py tests/unit/orchestrator/test_report_synthesis_service.py tests/unit/orchestrator/test_search_discovery_service.py tests/unit/orchestrator/test_search_helpers.py tests/unit/test_env_hygiene.py tests/unit/test_live_acceptance_framework.py services/orchestrator/tests/test_debug_pipeline_api.py services/orchestrator/tests/test_research_tasks_api.py -q` — passed.
- `python -m ruff check services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/services/search_discovery.py` — passed.
- `python -m black --check services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/services/search_discovery.py` — passed.
- `python -m mypy services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/services/search_discovery.py` — failed on existing imported-module typing issues in `services/orchestrator/app/research_quality/llm_assistance.py`, `services/orchestrator/app/reporting/markdown.py`, and `services/orchestrator/app/services/reporting.py`.

## 9. Risks and unknowns

- The resolver currently uses a bounded technical source profile catalog plus source-class rules; it improves common projects but is not a complete web authority graph.
- Direct authoritative URLs can still fail acquisition when upstream sites block or change paths.
- Search diagnostics remain JSON-in-existing-fields rather than a normalized query-attempt table.
- Pipeline runs no longer use authoritative-source resolver rows in the main `SEARCHING` loop; if a
  future live task needs resolver-backed URLs inside the worker path, add an explicit bounded budget
  or per-stage policy instead of re-enabling unconditional injection.

## 10. Rollback / recovery

Revert the touched code/docs/tests. No migration was added, so rollback is application-code only.
Existing task rows remain readable because new diagnostics are optional JSON keys.

## 11. Deferred work

- Optional self-hosted repository/package metadata resolver with caching and timeouts.
- Per-entity source quotas for broader comparison tasks.
- A normalized query-attempt diagnostics table if JSON payloads become too large.
