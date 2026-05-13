# Technical Explanation Search Quality

## 1. Objective

Refactor technical explanation research planning so queries such as `What is LangGraph and how does it work?` use answer-slot-driven query planning, source-role diversity, balanced acquisition ordering, and slot-aware grounded report inputs without changing the evidence ledger schema.

## 2. Why this exists

The current LangGraph planner output is source-aware but still shallow: it exposes only broad definition/mechanism/privacy/features slots and relies on a fixed guardrail query set. The product goal is deeper grounded reports, so the pipeline needs richer answer expectations and more systematic official/reference/repository source diversity before synthesis.

## 3. Scope

### In scope

- Add a deterministic `technical_explanation` answer-slot template.
- Generate slot-targeted query matrix metadata for technical explanation plans.
- Preserve target slot and expected source-role metadata through planned search queries.
- Add deterministic source-role metadata and role-balanced acquisition ordering for technical explanation tasks.
- Pass answer slots, slot coverage, claim slot grouping, and source-role diversity into grounded report inputs.
- Extend LangGraph live acceptance output with benchmark-style metrics.
- Update docs and this plan with validation.

### Out of scope

- MCP.
- Playwright or browser fallback changes.
- Worker queue, `dev.sh`, parsing, or infrastructure diagnostics unless directly blocked.
- Database migrations or new relational tables.
- Globally loosening generic article selection.

## 4. Constraints

- Preserve the ledger chain: `candidate_url -> fetch_attempt -> content_snapshot -> source_document -> source_chunk -> claim -> claim_evidence -> report_artifact`.
- Use existing JSON metadata seams only.
- Keep query count bounded by `RESEARCH_PLANNER_MAX_SEARCH_QUERIES`.
- Keep official, reference, repository, and high-quality secondary roles deterministic; LLM output can suggest but not override guardrails.
- Avoid optional weak-only gap churn.
- Do not add production dependencies.

## 5. Relevant files and systems

- `services/orchestrator/app/research_quality/answer_slots.py`
- `services/orchestrator/app/research_quality/source_intent.py`
- `services/orchestrator/app/research_quality/gap_analyzer.py`
- `services/orchestrator/app/planning/planner.py`
- `services/orchestrator/app/services/search_discovery.py`
- `services/orchestrator/app/services/acquisition.py`
- `services/orchestrator/app/services/claims.py`
- `services/orchestrator/app/reporting/grounded_llm.py`
- `services/orchestrator/app/reporting/markdown.py`
- `services/orchestrator/app/services/reporting.py`
- `scripts/live_acceptance_framework.py`
- tests under `tests/unit/orchestrator/`
- `docs/architecture.md`, `docs/runbook.md`

## 6. Milestones

### Milestone 1
- intent: make technical explanation plans explicit and bounded.
- code changes: add technical slots, slot query matrix, source-role preferences, and planned-query metadata preservation.
- validation: planner and research-quality unit tests.

### Milestone 2
- intent: select technical explanation sources by role balance instead of one rank score only.
- code changes: add source-role metadata and acquisition role interleaving/quotas.
- validation: acquisition ordering tests for LangGraph official docs/reference/repository and secondary-source caps.

### Milestone 3
- intent: improve grounded synthesis inputs and benchmark observability.
- code changes: add source-role diversity and slot-grouped claims to report grounding; add LangGraph acceptance benchmark metrics.
- validation: report/unit tests plus live LangGraph acceptance.

## 7. Implementation log

- 2026-05-13 / baseline audit:
  - changes: captured deterministic LangGraph planner output and ran pre-change live LangGraph acceptance.
  - rationale: establish before behavior before changing planner/source selection.
  - validation: `python scripts/live_acceptance.py --profile langgraph-technical-explanation --base-url http://127.0.0.1:8000 --artifact-dir /tmp/deepsearch-live-langgraph-before --json-output /tmp/deepsearch-live-langgraph-before.json --timeout-seconds 900` passed for task `9172105a-5b5e-461a-87bf-769803baa34d`.
  - next: implement milestones 1-3 and rerun targeted tests plus after acceptance.
- 2026-05-13 / implementation:
  - changes: added the `technical_explanation` slot template, bounded LangGraph/generic query matrices with `target_slots` and `source_role` metadata, source-role classification, role-balanced acquisition ordering, technical claim slot/source-role diversification, source-role-aware report grounding, and LangGraph benchmark metrics.
  - validation: targeted unit tests, ruff, black, and live LangGraph acceptance passed.
  - live after artifact: `/tmp/deepsearch-live-langgraph-after2`; recomputed benchmark at `/tmp/deepsearch-live-langgraph-after2/benchmark.recomputed.json`.
- 2026-05-14 / component-focus live parity:
  - changes: report eligibility now merges live-shaped nested `claim.notes_json.evidence_candidate.slot_ids`, can use verified evidence source URL/role metadata when component-focus eligibility lacks top-level fields, and emits component-focus evaluated/rescued/failed/missing-metadata diagnostics into report manifests, `REPORTING` events, and task observability.
  - rationale: archived live LangGraph runs showed supported official StateGraph limitations claims excluded only by `query_focus_mismatch`, while the current workspace helper would rescue the fully populated stability-run shape; diagnostics are needed to distinguish stale runtime from missing live metadata.
  - validation: requested targeted tests, ruff, runtime import fingerprinting, and one LangGraph live acceptance passed.
  - live after artifact: `.run/live_acceptance_langgraph_component_focus_live_parity`, task `5339ad3d-4fee-488c-a4dd-b5ee89dcc4cd`.
  - next: monitor whether future live runs with no supported official limitations claim still leave the optional limitations slot weak.

## 8. Validation

- Before baseline:
  - `python scripts/live_acceptance.py --profile langgraph-technical-explanation --base-url http://127.0.0.1:8000 --artifact-dir /tmp/deepsearch-live-langgraph-before --json-output /tmp/deepsearch-live-langgraph-before.json --timeout-seconds 900` - passed.
- Planned after implementation:
  - `pytest tests/unit/orchestrator/test_research_planner.py tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_acquisition_service.py` - passed.
  - `ruff check ...` on touched Python files - passed.
  - `black --check ...` on touched Python files - passed.
  - `python scripts/live_acceptance.py --profile langgraph-technical-explanation --base-url http://127.0.0.1:8000 --artifact-dir /tmp/deepsearch-live-langgraph-after2 --json-output /tmp/deepsearch-live-langgraph-after2.json --timeout-seconds 900` - passed for task `bb023296-346c-4776-8b68-8c0ccbf03983`.
- Component-focus live parity:
  - `python -m py_compile services/orchestrator/app/services/reporting.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/api/routes/research_tasks.py services/orchestrator/app/api/schemas/research_tasks.py` - passed.
  - `pytest tests/unit/orchestrator/test_report_synthesis_service.py -q` - passed.
  - `pytest tests/unit/orchestrator/test_report_synthesis_service.py tests/unit/orchestrator/test_technical_limitations_scoring.py tests/unit/orchestrator/test_candidate_target_slot_propagation.py -q` - passed.
  - `ruff check services/orchestrator/app/api/routes/health.py services/orchestrator/app/services/reporting.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/api/routes/research_tasks.py services/orchestrator/app/api/schemas/research_tasks.py tests/unit/orchestrator/test_report_synthesis_service.py` - passed.
  - `DEEPSEARCH_LIVE_ACCEPTANCE_HTTP_TIMEOUT=180 BROWSER_FETCH_BACKEND=none python scripts/live_acceptance.py --profile langgraph-technical-explanation --wait-seconds 900 --artifact-dir .run/live_acceptance_langgraph_component_focus_live_parity` - passed for task `5339ad3d-4fee-488c-a4dd-b5ee89dcc4cd`.

## 9. Risks and unknowns

- More required technical slots can increase gap-round pressure if claim drafting cannot classify evidence precisely enough.
- Live search results are variable, so source-role quotas must improve ordering without making completion depend on a single URL.
- LLM report writer may still fall back; deterministic report must remain slot-aware and grounded.

## 10. Rollback / recovery

Revert the touched planner/research-quality/acquisition/reporting/script/docs files. No migration or stored data rollback is required because all new data uses existing JSON metadata fields and task events.

## 11. Deferred work

- Persisted source-role tables or query-source association tables.
- Semantic entailment verification.
- LLM-driven gap planning.
- UI controls for editing source-role quotas.
