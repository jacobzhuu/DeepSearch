# Six-Task Acceptance Quality Fixes

## 1. Objective

Tighten general DeepSearch report quality after a six-task live acceptance run by validating LLM-assisted evidence reranking output, making claim review non-accepting for weak structured decisions, filtering report-eligible claims explicitly, and improving claim-source diversity for overview and comparison questions.

## 2. Why this exists

The live acceptance set completed end to end, but exposed general quality failures: score-only evidence reranker output was treated as fully successful, reviewer-rejected or adjacent claims entered the main report, report eligibility was not explicit in claim notes, and comparison/mechanism reports were too shallow despite persisted source evidence.

## 3. Scope

### In scope

- LLM evidence reranker quality diagnostics and fallback behavior.
- LLM claim reviewer prompt and decision-quality normalization.
- Report eligibility filtering for main report claims.
- Generic claim-drafting chunk diversity across parsed sources.
- Tests and docs for the changed quality contracts.

### Out of scope

- New search providers or paid search APIs.
- Database migrations.
- Entity/domain hard-coding for the six acceptance queries.
- UI styling changes.
- New LLM-authored claims or unsupported facts.

## 4. Constraints

- Keep every LLM-assisted stage optional and fallback-safe.
- Preserve the persisted source/chunk/citation/claim/evidence ledger.
- Do not make DeepSeek a search provider.
- Keep smoke/local mode working without LLM.
- Use existing JSON seams only.

## 5. Relevant files and systems

- `services/orchestrator/app/research_quality/llm_assistance.py`
- `services/orchestrator/app/services/claims.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/services/reporting.py`
- `services/orchestrator/app/reporting/grounded_llm.py`
- `services/orchestrator/app/reporting/markdown.py`
- `tests/unit/orchestrator/test_llm_assistance.py`
- `tests/unit/orchestrator/test_report_synthesis_service.py`
- `tests/unit/orchestrator/test_pipeline_worker.py`
- `docs/api.md`, `docs/runbook.md`, `docs/schema.md`

## 6. Milestones

### Milestone 1
- intent: validate LLM assistance quality rather than accepting weak structured output.
- code changes: evidence reranker low-quality fallback; claim reviewer quality normalization.
- validation: targeted LLM assistance tests.

### Milestone 2
- intent: keep rejected, adjacent, off-topic, and weak claims out of the main report.
- code changes: report eligibility helper, claim-note diagnostics, LLM report writer receives only eligible claims.
- validation: report synthesis and report rendering tests.

### Milestone 3
- intent: improve general source diversity and claim depth without entity-specific rules.
- code changes: chunk selection includes high-quality chunks across source documents before filling by rank.
- validation: worker/source selection unit test plus smoke/no-LLM checks.

### Milestone 4
- intent: prove the fixes on the same six live tasks.
- code changes: none expected.
- validation: rerun six acceptance tasks and compare before/after.

## 7. Implementation log

- 2026-05-06 / before-fix evaluation:
  - changes: ran six live tasks sequentially through `/run` using SearXNG, OpenSearch, filesystem snapshots, and DeepSeek-enabled LLM assistance.
  - rationale: establish evidence before coding and avoid overfitting to one LangGraph regression.
  - validation: all six tasks reached `COMPLETED`; artifacts saved under `/tmp/deepsearch-six-task-acceptance-before-20260506T101919Z`.
- 2026-05-06 / implementation:
  - changes: added evidence-reranker low-quality validation, claim-review decision normalization, explicit report eligibility diagnostics, reviewer/focus/slot-aware report filtering, source-diverse claim chunk selection, resilient SearXNG fallback/continued-query behavior, and smoke/count consistency coverage.
  - rationale: address the common failure classes from the six-task run without hard-coding entities, domains, URLs, or paid search providers.
  - validation: targeted LLM-assistance, report-synthesis, source-judge, worker, smoke/count, ruff, black, and py_compile checks passed.
  - next: rerun the six live acceptance tasks on the restarted worker and compare before/after.
- 2026-05-06 / first after-fix reruns:
  - changes: no task-specific code; live reruns exposed two general follow-up issues: weak reviewer diagnostics were being persisted as claim vetoes, and SearXNG rate-limited first-query failures aborted before later planned queries.
  - rationale: both were general quality/robustness failures, not entity-specific regressions.
  - validation: added targeted tests for low-quality reviewer fallback, continued search after a failed planned query, and SearXNG resilient-engine retry.
  - next: final six-task rerun after restart.
- 2026-05-06 / final live rerun:
  - changes: none after rerun.
  - validation: live artifacts saved under `/tmp/deepsearch-six-task-acceptance-after-final-20260506T165919Z`.
  - result: acceptance did not pass. SearXNG general engines were rate-limited; the resilient free-engine retry avoided immediate first-query aborts but returned mostly irrelevant MDN/StackOverflow/GitHub/arXiv snippets for this task set. Five tasks failed before claim generation; one LangGraph task failed during supplemental research after producing only three claims.
  - next: defer larger source-planning/search backend work rather than adding entity/domain hard-coding.

## 8. Validation

- `python3 -m py_compile services/orchestrator/app/research_quality/llm_assistance.py services/orchestrator/app/services/reporting.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/services/claims.py tests/unit/orchestrator/test_llm_assistance.py tests/unit/orchestrator/test_report_synthesis_service.py services/orchestrator/tests/test_debug_pipeline_api.py`
- `python3 -m pytest tests/unit/orchestrator/test_llm_assistance.py -q`
- `python3 -m pytest tests/unit/orchestrator/test_report_synthesis_service.py -q`
- `python3 -m pytest tests/unit/orchestrator/test_pipeline_worker.py -q`
- `python3 -m pytest services/orchestrator/tests/test_debug_pipeline_api.py::test_run_endpoint_queue_is_consumed_by_host_local_worker -q`
- `python3 -m pytest tests/unit/orchestrator/test_source_judge.py -q`
- `python3 -m pytest tests/unit/orchestrator/test_search_discovery_service.py -q`
- `python3 -m pytest tests/unit/orchestrator/test_search_helpers.py -q`
- `python3 -m pytest tests/unit/orchestrator/test_llm_assistance.py tests/unit/orchestrator/test_report_synthesis_service.py tests/unit/orchestrator/test_search_discovery_service.py tests/unit/orchestrator/test_search_helpers.py services/orchestrator/tests/test_debug_pipeline_api.py::test_run_endpoint_queue_is_consumed_by_host_local_worker tests/unit/orchestrator/test_source_judge.py tests/unit/orchestrator/test_pipeline_worker.py -q`
- `python3 -m ruff check services/orchestrator/app/research_quality/llm_assistance.py services/orchestrator/app/services/reporting.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/services/claims.py services/orchestrator/app/services/search_discovery.py services/orchestrator/app/search/providers.py services/orchestrator/app/reporting/grounded_llm.py tests/unit/orchestrator/test_llm_assistance.py tests/unit/orchestrator/test_report_synthesis_service.py services/orchestrator/tests/test_debug_pipeline_api.py tests/unit/orchestrator/test_search_discovery_service.py tests/unit/orchestrator/test_search_helpers.py`
- `python3 -m black --check services/orchestrator/app/research_quality/llm_assistance.py services/orchestrator/app/services/reporting.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/services/claims.py services/orchestrator/app/services/search_discovery.py services/orchestrator/app/search/providers.py services/orchestrator/app/reporting/grounded_llm.py tests/unit/orchestrator/test_llm_assistance.py tests/unit/orchestrator/test_report_synthesis_service.py services/orchestrator/tests/test_debug_pipeline_api.py tests/unit/orchestrator/test_search_discovery_service.py tests/unit/orchestrator/test_search_helpers.py`
- six-task live acceptance rerun against `http://127.0.0.1:8000`

## 9. Risks and unknowns

- Generic subject-focus filtering can exclude terse but valid claims that omit the queried entity name.
- Source diversity can improve candidate coverage but cannot invent evidence if parsing or retrieval misses useful source text.
- LLM reviewer behavior remains provider-dependent; deterministic fallback must stay conservative.

## 10. Rollback / recovery

Revert the touched code and docs. No migration is introduced, so rollback is application-code only. Existing task artifacts remain readable because new diagnostics use optional JSON keys.

## 11. Deferred work

- Richer multi-entity planning and source quotas for comparison tasks.
- Persistent source-judge/claim-review tables if JSON diagnostics become too large.
- Stronger semantic entailment verifier.
