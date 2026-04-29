# Planner-Enabled Deterministic Research Quality Layer

## 1. Objective

Improve planner-enabled runs so an LLM plan can guide search without controlling evidence generation, source selection, claim drafting, verification, or report writing. The implementation will add deterministic guardrails for overview/definition queries, answer-yield-aware supplemental acquisition, stronger HTML extraction cleanup, category-aware claim/report selection, frontend observability, a manual smoke script, and regression coverage.

## 2. Why this exists

The current planner can generate a `research_plan.created` event, but a real SearXNG query for `What is SearXNG and how does it work?` failed because source selection attempted low-yield pages and never fetched the about or Wikipedia sources needed for deterministic evidence-backed claims. The platform needs a quality layer that treats planner output as bounded advice and keeps the research loop evidence-first, auditable, and recoverable.

## 3. Scope

### In scope

- Overview/definition intent classification and query-plan sanitization.
- Deterministic source category scoring and SearXNG known-path hints for SearXNG overview runs only.
- One bounded supplemental acquisition pass when claim drafting yields no or insufficient answer coverage.
- Answer-yield diagnostics stored in existing events and metadata.
- HTML extraction link-text preservation and conservative broken-fragment cleanup.
- Category-aware deterministic claim drafting and report rendering.
- Task Detail observability for planner, final queries, source selection, coverage, supplemental acquisition, and failure diagnostics.
- Host-local smoke script for one full planner pipeline run.
- Tests and docs updates.

### Out of scope

- LangGraph, worker queues, Celery, background jobs, browser fallback, Playwright/Selenium, PDF/Tika, and schema migrations.
- LLM-written claims, verification decisions, or reports.
- Generalized URL invention for arbitrary projects beyond the explicit SearXNG known-path hint.
- Multi-round research or a planner-driven gap analyzer.

## 4. Constraints

- No database schema migration unless a hard blocker appears; diagnostics should use existing `task_event.payload_json`, `candidate_url.metadata_json`, `source_chunk.metadata_json`, and `claim.notes_json`.
- No-LLM deterministic baseline must keep existing behavior and avoid planner observability.
- DeepSeek/OpenAI-compatible planner failures must fall back to the original query.
- All claims and reports must remain deterministic and evidence-backed.
- Do not commit secrets, `.env`, `orchestrator.log`, `dist`, or node_modules noise.
- Preserve existing SSRF and parsing regressions.

## 5. Relevant files and systems

- `services/orchestrator/app/planning/`
- `services/orchestrator/app/services/search_discovery.py`
- `services/orchestrator/app/services/acquisition.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/services/claims.py`
- `services/orchestrator/app/claims/drafting.py`
- `services/orchestrator/app/parsing/`
- `services/orchestrator/app/reporting/`
- `services/orchestrator/app/settings.py`
- `apps/web/src/pages/tasks/TaskDetailPage.tsx`
- `apps/web/src/types/api.ts`
- `.env.example`
- `scripts/`
- `docs/architecture.md`, `docs/api.md`, `docs/schema.md`, `docs/runbook.md`
- backend unit and pipeline tests, frontend build

## 6. Milestones

### Milestone 1
- intent: planner guardrails and source-selection metadata.
- code changes: classify overview intent, sanitize final search query lists, override Wikipedia avoid for overview, classify source categories, add SearXNG known-path candidates, and surface metadata.
- validation: planner and search-discovery/source-selection tests.

### Milestone 2
- intent: answer-yield-aware acquisition and failure diagnostics.
- code changes: compute per-source answer-yield metrics, trigger one supplemental acquisition pass, persist supplemental observability, and enrich `DRAFTING_CLAIMS` failure details.
- validation: pipeline tests for low-yield first sources, supplemental attempt, no loop, and diagnostic failure.

### Milestone 3
- intent: extraction, claim, and report quality.
- code changes: preserve link text, clean broken residues conservatively, reject low-yield pages and bad candidates, categorize claims, dedupe near duplicates, and render answer-focused Markdown sections.
- validation: parsing helper tests, claim drafting tests, report markdown tests.

### Milestone 4
- intent: operator observability and host-local smoke workflow.
- code changes: Task Detail UI fields, API types, `.env.example` defaults, smoke script, docs updates.
- validation: frontend build, script clean-failure test, docs consistency.

### Milestone 5
- intent: full regression validation.
- code changes: formatting or small fixes found by tests.
- validation: `python3 -m pytest -q`, `python3 -m ruff check .`, `python3 -m black --check .`, and `cd apps/web && npm run build`.

## 7. Implementation log

- 2026-04-28 / session:
  - changes: created this ExecPlan after reading the required project docs and confirming the task spans planner, source selection, acquisition, parsing, claims, report, frontend, scripts, and docs.
  - rationale: PLANS.md requires an active plan for this cross-subsystem, workflow-sensitive change.
  - validation: pending.
  - next: inspect current planner/source-selection/claim/report/frontend implementation and implement Milestone 1.
- 2026-04-28 / implementation:
  - changes: implemented overview/definition planner guardrails, final search-query sanitization, Wikipedia avoid override, SearXNG known-path candidates, source categories and downranking, planner/source-selection observability, and planner-enabled acquisition source targets.
  - rationale: planner output must remain advice while deterministic rules preserve official/about/reference sources for evidence-backed reports.
  - validation: covered by planner, search-discovery, acquisition, and pipeline regression tests.
  - next: complete answer-yield recovery, extraction, claims, report, frontend, smoke script, and docs.
- 2026-04-28 / implementation:
  - changes: added per-source answer-yield metrics, one bounded supplemental acquisition pass, enriched `DRAFTING_CLAIMS` failure details, Sphinx link-text preservation metadata, conservative broken-link cleanup, claim categories, near-duplicate claim suppression, and answer-focused report sections.
  - rationale: low-yield initial pages should not make the pipeline fail without trying unattempted high-value evidence sources, and successful reports must be readable without unsupported report writing.
  - validation: covered by parsing, claim drafting, report markdown/synthesis, and debug pipeline tests.
  - next: expose observability in the frontend and add operator smoke tooling.
- 2026-04-28 / closeout:
  - changes: updated Task Detail observability UI/API types, added `scripts/smoke_planner_pipeline.py`, added script clean-failure test, updated docs and environment examples, and formatted touched Python files.
  - rationale: operators need to see planner decisions, source quality, answer coverage, supplemental acquisition, and failure details without inspecting raw logs.
  - validation: full validation commands passed; manual live smoke remains an operator command because the local API service was not started inside this turn.
  - next: run live SearXNG/DeepSeek smoke when the orchestrator and backing services are running.
- 2026-04-28 / quality-layer refinement:
  - changes: added a shared deterministic `answer_role` / `answer_relevant` scoring layer for claim drafting, report filtering, and source-yield diagnostics; tightened overview-query rejection of navigation, project-meta, documentation-pointer, setup, diagram/config, and off-query `other` candidates; added tests for official about-style positives, negative noise, near-duplicate features, parsing pointer noise, and answer-yield counts.
  - rationale: planner-enabled runs need a reusable answer-focused evidence-yield criterion so high-value official sources can contribute claims when they contain answer evidence, while complete but non-answer sentences do not pollute the ledger.
  - validation: `python3 -m pytest -q`, `python3 -m ruff check .`, `python3 -m black --check .`, and `cd apps/web && npm run build` passed.
  - next: run live planner-mode smoke against a running orchestrator/Search/OpenSearch stack when operator services are available.
- 2026-04-28 / P0 generalization pass:
  - changes: introduced `services/orchestrator/app/research_quality/` with shared source-intent classification and answer-slot coverage contracts, rewired acquisition and pipeline diagnostics to consume it, exposed answer slots in task observability and report manifests, added `scripts/benchmark_queries.py` for the ten-query generalization benchmark, and fixed stale smoke/audit documentation.
  - rationale: the SearXNG-specific quality layer needed a reusable contract before adding more query-specific patches.
  - validation: targeted quality/acquisition/planner/report/benchmark tests passed; `python3 -m pytest -q`, targeted `ruff`/`black --check`, `npm run build`, and `python3 scripts/benchmark_queries.py --json` passed.
  - next: run live planner-mode and benchmark `--run` smoke against a running orchestrator/Search/OpenSearch stack when operator services are available.
- 2026-04-28 / P1-P2 stabilization pass:
  - changes: added `research_quality/evidence.py` as the code-level evidence-candidate/source-yield/evidence-yield/slot-coverage contract; serialized candidate lineage into `claim.notes_json`; exposed source yield, dropped sources, evidence yield, slot coverage, and verification summaries through task events, task detail observability, report manifests, benchmark `--run` JSON, and the Task Detail UI; tightened deterministic verification to distinguish strong support from weak lexical support, shallow overlap, numeric/date mismatch, and scope mismatch.
  - rationale: the system needed traceable answer-slot -> evidence candidate -> claim -> verification -> report diagnostics without a database migration or LLM-written claims/reports.
  - validation: targeted evidence/claim/verification/report/debug-pipeline/benchmark tests passed during implementation; full validation pending in this turn.
  - next: run full pytest, targeted ruff/black, benchmark JSON listing, and frontend build.
- 2026-04-29 / benchmark and compatibility closeout:
  - changes: ran the live two-query benchmark against the current working-tree API on an isolated port using the real SearXNG/OpenSearch/planner configuration; added stable diagnostic contract constants; normalized missing evidence/verification summaries to `{}`; kept old claim notes without evidence lineage reportable through persisted citation spans; added tests for legacy task observability, legacy claim report synthesis, benchmark missing-field normalization, and cross-surface contract field usage.
  - rationale: the prior P1/P2 pass had full unit coverage but had not proven the real `benchmark --run` path, and new diagnostics needed explicit backward compatibility for older tasks and manifests.
  - validation: targeted compatibility/contract tests passed; full validation pending after this plan update.
  - next: run full pytest, targeted ruff/black, benchmark listing, benchmark run, and frontend build.
- 2026-04-29 / release-prep hardening:
  - changes: added `/versionz` process diagnostics, upgraded smoke search/acquisition to deterministic synthetic `deepsearch-smoke.local` fixtures, added benchmark `--query-id`/`--only`/`--output`, canonicalized report manifest `verifier_method`, and documented restart/version checks.
  - rationale: operators need to distinguish stale port-8000 processes from current checkout code, and smoke/local mode must prove the deterministic pipeline can complete without relying on weak `example.com` content.
  - validation: full validation passed against current code; default port 8000 was confirmed stale and later failed one old-service benchmark task during search timeout.
  - next: clean generated artifacts from the commit scope and restart the real host-local service from the current checkout before using port 8000 as the primary validation endpoint.

## 8. Validation

- `python3 -m pytest -q` - passed on 2026-04-28
- `python3 -m ruff check .` - passed on 2026-04-28
- `python3 -m black --check .` - passed on 2026-04-28
- `cd apps/web && npm run build` - passed on 2026-04-28
- `python3 scripts/benchmark_queries.py --json` - passed on 2026-04-28
- P1/P2 targeted tests on 2026-04-28:
  - `python3 -m pytest tests/unit/orchestrator/test_evidence_quality.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_claim_verification_helpers.py tests/unit/orchestrator/test_claim_verification_service.py tests/unit/orchestrator/test_report_markdown.py tests/unit/orchestrator/test_report_synthesis_service.py services/orchestrator/tests/test_debug_pipeline_api.py tests/unit/test_benchmark_queries_script.py -q` - passed
- P1/P2 closeout validation on 2026-04-28:
  - `python3 -m pytest -q` - passed
  - targeted `python3 -m ruff check ...` over changed backend/script/test Python paths - passed
  - targeted `python3 -m black --check ...` over changed backend/script/test Python paths - passed after formatting five files
  - `python3 scripts/benchmark_queries.py --json` - passed
  - `cd apps/web && npm run build` - passed
- Benchmark and compatibility validation on 2026-04-29:
  - `python3 scripts/benchmark_queries.py --run --limit 2 --base-url http://127.0.0.1:18081 --json` - passed against current working-tree API with `running_mode=real-search+opensearch+planner-LLM`; both benchmark tasks completed and returned slot coverage, source yield, evidence yield, and verification summaries
  - `python3 scripts/benchmark_queries.py --run --limit 2 --base-url http://127.0.0.1:18080 --json` - failed in smoke/local mode because `SEARCH_PROVIDER=smoke` only returns `example.com`, so strict claim drafting correctly produced no claims; diagnostics were populated through the failure path
  - `python3 -m pytest tests/unit/orchestrator/test_evidence_quality.py tests/unit/orchestrator/test_report_synthesis_service.py services/orchestrator/tests/test_debug_pipeline_api.py tests/unit/test_benchmark_queries_script.py -q` - passed
  - `python3 -m pytest -q` - passed
  - `python3 -m ruff check services/orchestrator/app scripts tests services/orchestrator/tests` - passed
  - `python3 -m black --check services/orchestrator/app scripts tests services/orchestrator/tests` - passed
  - `python3 scripts/benchmark_queries.py --json` - passed
  - `python3 scripts/benchmark_queries.py --run --limit 2 --base-url http://127.0.0.1:18081 --json` - passed again after compatibility fixes, using a restarted current-code API process
  - `python3 scripts/benchmark_queries.py --run --limit 2 --json` - passed against the existing default port 8000 service; that service completed both tasks but did not expose the newest summaries, and the updated script normalized those missing fields to `[]` or `{}`
  - `cd apps/web && npm run build` - passed
- Release-prep validation on 2026-04-29:
  - `curl -s http://127.0.0.1:8000/versionz` - returned `404`, confirming the existing port 8000 process is older than the version diagnostics endpoint
  - `curl -s http://127.0.0.1:8000/openapi.json | rg 'source_yield_summary|evidence_yield_summary|slot_coverage_summary|verification_summary|answer_slots|dropped_sources'` - returned no fields, confirming the existing port 8000 process does not expose the newest diagnostics schema
  - `python3 -m pytest -q` - passed
  - `python3 -m ruff check services/orchestrator/app scripts tests services/orchestrator/tests` - passed
  - `python3 -m black --check services/orchestrator/app scripts tests services/orchestrator/tests` - passed
  - `python3 scripts/benchmark_queries.py --json` - passed
  - `python3 scripts/benchmark_queries.py --json --query-id 3 --output /tmp/...json` - passed and wrote non-empty JSON output
  - `cd apps/web && npm run build` - passed
  - `python3 scripts/benchmark_queries.py --run --limit 2 --base-url http://127.0.0.1:18081 --json` - passed against current working-tree API with real SearXNG/OpenSearch/planner dependencies; both tasks completed with quality summaries
  - `python3 scripts/benchmark_queries.py --run --limit 2 --base-url http://127.0.0.1:18082 --json` - passed in smoke/local mode with synthetic `deepsearch-smoke.local` fixtures; both tasks completed with non-empty claims, evidence summaries, slot coverage, verification summary, and report artifacts
  - `python3 scripts/benchmark_queries.py --run --limit 2 --json` - failed against the stale existing port 8000 service because one old-service task timed out in `SEARCHING`; this is a stale-process finding, not a current-code failure
- manual smoke:
  - `python scripts/smoke_planner_pipeline.py --query "What is SearXNG and how does it work?" --base-url http://127.0.0.1:8000` - pending operator validation against a running service
- benchmark run:
  - `python scripts/benchmark_queries.py --run --base-url http://127.0.0.1:8000 --json` - still an operator command for the default service port; the current working-tree validation used `--base-url http://127.0.0.1:18081` to avoid ambiguity with any existing server on port 8000

## 9. Risks and unknowns

- Live search results may vary, so source-selection tests should use deterministic candidates rather than relying on external search.
- Supplemental acquisition must avoid creating hidden multi-round behavior; it should run at most once and stay bounded.
- Existing dirty workspace changes may already contain partial planner guardrails; implementation must work with them without reverting unrelated edits.
- Report readability must improve without inventing claims or hiding low coverage.
- Live planner-LLM quality still depends on the search provider returning at least one official SearXNG result so the deterministic known-path hint can activate.

## 10. Rollback / recovery

- Revert this plan plus touched backend/frontend/docs/script/test files from this turn.
- No migration rollback is expected because no relational schema change is planned.
- Disable planner behavior operationally with `LLM_ENABLED=false` and `RESEARCH_PLANNER_ENABLED=false`.

## 11. Deferred work

- Persisted `research_plan` and richer plan/audit schema.
- Generalized known-path source discovery for arbitrary software projects.
- Multi-round research gap analysis.
- More advanced semantic near-duplicate detection beyond deterministic normalization.
