# Technical Concept Source Quality

## 1. Objective

Improve deterministic no-LLM source selection, gap recovery, and claim-slot relevance for technical concept queries, using the exported LangGraph run as the regression case.

## 2. Why this exists

The real-search LangGraph task completed but produced weak coverage because generic "What is" pages were treated as official/about-like sources and because deterministic claim scoring did not recognize technical framework mechanism and feature language. Gap rounds also spent attempts on newly discovered low-value sources instead of falling back to already discovered high-value official/reference candidates.

## 3. Scope

### In scope

- Tighten source-intent classification so title-only generic tutorial pages are not official/about.
- Prefer official docs, reference docs, and upstream GitHub sources before generic articles for library/framework overview queries.
- Expand deterministic mechanism, feature, limitation, and trust/privacy terms for technical framework queries.
- Make gap rounds ignore newly discovered low-value candidates when high-value unattempted candidates already exist.
- Add focused unit tests for classification, ranking, claim scoring, and gap fallback candidate selection.
- Update docs that describe operator-facing deterministic quality behavior.

### Out of scope

- No async `/run` architecture changes.
- No Redis, Celery, browser fetch, PDF, Tika, embeddings, reranking, or LLM enabling.
- No database migrations.
- No broad frontend redesign or unrelated cleanup.
- No `.env` or secret changes.

## 4. Constraints

- Preserve `research_task` as the primary product object.
- Keep evidence and claims ledger-backed; do not create unsupported claims.
- Keep source and gap behavior deterministic and inspectable through existing task-event payloads.
- Preserve host-local/self-hosted operation as the primary route.
- Avoid new production dependencies.

## 5. Relevant files and systems

- `services/orchestrator/app/research_quality/source_intent.py`
- `services/orchestrator/app/claims/drafting.py`
- `services/orchestrator/app/parsing/quality.py`
- `services/orchestrator/app/services/acquisition.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/research_quality/gap_analyzer.py`
- `tests/unit/orchestrator/test_research_quality.py`
- `tests/unit/orchestrator/test_acquisition_service.py`
- `tests/unit/orchestrator/test_claim_drafting_helpers.py`
- `tests/unit/orchestrator/test_parsing_helpers.py`
- `services/orchestrator/tests/test_debug_pipeline_api.py`
- `docs/architecture.md`
- `docs/runbook.md`

## 6. Milestones

### Milestone 1
- intent: capture regression root cause from exported task diagnostics.
- code changes: add this ExecPlan and record inspected artifacts.
- validation: summarize observed selected, attempted, unattempted, and claim-slot data.

### Milestone 2
- intent: fix source classification and fetch priority for library/framework overview queries.
- code changes: make official/about classification require official-like domain/path context, add query-subject awareness, and keep official docs/reference/GitHub ahead of generic sources.
- validation: source-intent and acquisition ordering tests.

### Milestone 3
- intent: improve deterministic claim-slot relevance for technical concept queries.
- code changes: expand mechanism/feature/privacy-trust term sets and scoring expectations.
- validation: claim-helper tests for LangGraph mechanism, feature, and trust sentences.

### Milestone 4
- intent: improve gap fallback when supplemental search returns duplicates or low-value new URLs.
- code changes: select high-value newly discovered gap candidates only; otherwise fetch existing unattempted high-value candidates.
- validation: targeted debug-pipeline helper test.

### Milestone 5
- intent: document and validate.
- code changes: update docs and plan log.
- validation: targeted pytest, ruff, black, and diff checks.

## 7. Implementation log

- 2026-04-30 / session:
  - changes: created this ExecPlan after inspecting the exported LangGraph task artifacts.
  - rationale: the change spans source classification, acquisition ranking, claim scoring, gap behavior, docs, and tests.
  - validation: pending.
  - next: patch source-intent and claim-scoring helpers.
- 2026-04-30 / session:
  - changes: tightened query-subject-aware source classification, demoted generic "What is" tutorial pages, promoted matching official docs/reference/GitHub candidates ahead of generic articles, added generic technical framework planner terms, expanded mechanism/feature/trust claim scoring, and made gap rounds skip low-value new URLs before falling back to existing high-value unattempted candidates.
  - rationale: exported LangGraph diagnostics showed GeeksForGeeks and IBM were promoted as `official_about`, official/reference/GitHub candidates remained unattempted, and LangGraph state/nodes/edges/workflow evidence was rejected as `other`.
  - validation: targeted pytest, ruff, and black checks passed; see Validation.
  - next: run `git diff --check` and final review.
- 2026-04-30 / resume:
  - changes: re-read the required project docs, re-inspected the exported LangGraph artifacts, added source-quality consistency for `reference.` and `documentation.` docs domains, and added a focused parsing-quality regression test.
  - rationale: reference docs should not be prioritized for fetch and then downgraded to generic source quality after parsing.
  - validation: targeted pytest, ruff, black check, and `git diff --check` passed; see Validation.
  - next: final handoff.
- 2026-04-30 / manual-acceptance follow-up:
  - changes: re-inspected the failed post-fix LangGraph diagnostics, added owned project-domain and upstream GitHub matching for LangGraph, demoted localized mirrors to `secondary_reference`, demoted third-party GitHub repos to secondary/generic treatment, blocked job/freelance/listing pages for overview queries, made parsing source quality consume source-selection categories, mapped Chinese framework/state/workflow claims into definition/mechanism/trust slots, cleaned leading dash fragments from persisted definition claims, summed task-detail fetch counters across initial and gap acquisition, and added LangGraph targeted gap queries for LangChain docs/reference/GitHub.
  - rationale: the failed run attempted `github.langchain.ac.cn` first as `official_about`, used localized mirrors as official-like evidence, ranked third-party GitHub tutorials too highly, showed stale per-round fetch counters, and rejected accepted-looking stateful workflow claims as not answer-focused.
  - validation: targeted pytest, ruff, black check, and `git diff --check` passed in this follow-up.
  - next: final handoff.

## 8. Validation

- `python3 -m pytest tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_acquisition_service.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_gap_analyzer.py tests/unit/orchestrator/test_research_planner.py tests/unit/orchestrator/test_parsing_helpers.py services/orchestrator/tests/test_debug_pipeline_api.py -q` — passed
- `python3 -m ruff check services/orchestrator/app/research_quality/source_intent.py services/orchestrator/app/claims/drafting.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/planning/planner.py services/orchestrator/app/llm/providers.py services/orchestrator/app/parsing/quality.py tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_acquisition_service.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_gap_analyzer.py tests/unit/orchestrator/test_research_planner.py tests/unit/orchestrator/test_parsing_helpers.py services/orchestrator/tests/test_debug_pipeline_api.py` — passed
- `python3 -m black --check services/orchestrator/app/research_quality/source_intent.py services/orchestrator/app/claims/drafting.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/planning/planner.py services/orchestrator/app/llm/providers.py services/orchestrator/app/parsing/quality.py tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_acquisition_service.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_gap_analyzer.py tests/unit/orchestrator/test_research_planner.py tests/unit/orchestrator/test_parsing_helpers.py services/orchestrator/tests/test_debug_pipeline_api.py` — passed
- `git diff --check` — passed
- `python3 -m pytest tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_acquisition_service.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_gap_analyzer.py tests/unit/orchestrator/test_parsing_helpers.py services/orchestrator/tests/test_debug_pipeline_api.py -q` — passed in the manual-acceptance follow-up
- `python3 -m pytest tests/unit/orchestrator/test_research_planner.py tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_gap_analyzer.py -q` — passed in the manual-acceptance follow-up
- `python3 -m ruff check services/orchestrator/app/research_quality/source_intent.py services/orchestrator/app/research_quality/gap_analyzer.py services/orchestrator/app/claims/drafting.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/services/parsing.py services/orchestrator/app/api/routes/research_tasks.py services/orchestrator/app/parsing/quality.py tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_acquisition_service.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_gap_analyzer.py tests/unit/orchestrator/test_parsing_helpers.py services/orchestrator/tests/test_debug_pipeline_api.py` — passed in the manual-acceptance follow-up
- `python3 -m black --check services/orchestrator/app/research_quality/source_intent.py services/orchestrator/app/research_quality/gap_analyzer.py services/orchestrator/app/claims/drafting.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/services/parsing.py services/orchestrator/app/api/routes/research_tasks.py services/orchestrator/app/parsing/quality.py tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_acquisition_service.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_gap_analyzer.py tests/unit/orchestrator/test_parsing_helpers.py services/orchestrator/tests/test_debug_pipeline_api.py` — passed in the manual-acceptance follow-up after formatting `source_intent.py` and `test_gap_analyzer.py`
- `git diff --check` — passed in the manual-acceptance follow-up
- `python3 scripts/benchmark_queries.py --run --base-url http://127.0.0.1:8000 --query-id 3 --wait-seconds 420 --output /tmp/deepsearch-langgraph-quality-fix-final-rerun.json` — passed in the manual-acceptance follow-up; task `2a3ef035-7ecf-46ec-af54-288bca2df027` completed in `real-search+opensearch+no-LLM` mode with definition, mechanism, privacy, and features all covered.

## 9. Risks and unknowns

- Query-subject heuristics can only approximate official ownership without a curated domain registry.
- GitHub access may still fail in some host networks; the fix ensures ordering and fallback, not external reachability.
- The deterministic verifier remains lexical and may still miss deeper mechanism claims.

## 10. Rollback / recovery

- Revert this plan and the touched Python/docs/tests files.
- No migration rollback is required.
- Existing tasks remain readable because changes only affect future scoring/selection behavior and existing JSON payloads are backward-compatible.

## 11. Deferred work

- Curated project-domain registry.
- Browser fetch fallback for pages that block plain HTTP fetch.
- PDF/Tika parsing.
- Embedding or hybrid retrieval.
- LLM planning, claim drafting, verification, or report writing.
