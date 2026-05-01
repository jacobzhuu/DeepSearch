# P1 Evidence Credibility Enhancement

## 1. Objective

Deliver the smallest deterministic no-LLM evidence credibility layer that makes source quality,
claim verification, evidence selection, citation precision, source deduplication diagnostics, and
quality benchmarking auditable from the existing DeepSearch pipeline.

## 2. Why this exists

P0 proved the host-local product loop can run end to end. P1 raises the trust bar: reports should
not look complete merely because the pipeline produced claims. The system must expose why sources
and evidence are strong or weak, keep weak evidence out of supported conclusions, and give the
operator benchmark metrics that show evidence quality without hardcoded answers.

## 3. Scope

### In scope

- deterministic source-document and source-chunk quality scoring using existing ledger fields and
  metadata
- metadata-first audit details for source quality, chunk density, verification reasons, and
  evidence ranking
- stricter claim verification statuses: `supported`, `unsupported`, `mixed`, and `contradicted`
- relation-level evidence ranking that considers claim relevance, source quality, information
  density, and domain diversity
- sentence-level citation-span selection with explicit fallback metadata when exact sentence spans
  cannot be selected
- stronger URL canonicalization and duplicate-content diagnostics that reduce duplicate evidence
- API/report/frontend visibility for key audit fields without rewriting the UI
- a lightweight evidence-quality benchmark over 3-5 real questions that outputs JSON and Markdown
- unit and service tests for the risky trust decisions
- P1.5 closeout for more precise citation spans, cross-claim evidence reuse downranking, and richer
  benchmark diagnostics

### Out of scope

- LLM planner or LLM report writer changes
- new queue system or distributed leases
- broad pipeline rewrite
- embeddings, reranking models, or heavy retrieval infrastructure
- browser/Tika/PDF support
- multi-tenant trust policies

## 4. Constraints

- preserve deterministic no-LLM alpha behavior
- keep the current `research_task` product path and worker flow
- avoid schema migration unless existing relational fields and metadata are insufficient
- keep provenance traceable through existing `source_document`, `source_chunk`,
  `citation_span`, `claim`, `claim_evidence`, and `report_artifact` records
- no new production dependency without a narrow justification
- do not hardcode benchmark answers
- docs must describe heuristic limits honestly

## 5. Relevant files and systems

- `services/orchestrator/app/research_quality/`
- `services/orchestrator/app/claims/`
- `services/orchestrator/app/services/claims.py`
- `services/orchestrator/app/services/parsing.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/search/canonicalize.py`
- `services/orchestrator/app/reporting/`
- `services/orchestrator/app/api/routes/`
- `services/orchestrator/app/api/schemas/`
- `apps/web/src/pages/tasks/`
- `scripts/benchmark_queries.py`
- new/updated evidence benchmark script
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/architecture.md`

## 6. Milestones

### Milestone 1
- intent: inspect current trust-related code and define a metadata-first design
- code changes: none or plan/docs only
- validation: plan updated with findings and implementation targets

### Milestone 2
- intent: compute and persist deterministic source/chunk quality
- code changes: research-quality helper, parsing/source persistence metadata, API read models
- validation: unit tests for scoring and source API output

### Milestone 3
- intent: tighten claim verification and evidence selection
- code changes: verification helper/service changes, relation/status audit metadata, citation
  precision metadata
- validation: tests for supported/unsupported/mixed/contradicted and weak evidence rejection

### Milestone 4
- intent: expose auditability in report/API/frontend
- code changes: report sections/manifest, task detail or source/claim views, docs
- validation: report and API tests

### Milestone 5
- intent: add evidence quality benchmark
- code changes: benchmark script with JSON and Markdown output
- validation: local smoke or mock-compatible benchmark execution

### Milestone 6
- intent: close P1 benchmark gaps without changing architecture
- code changes: improve sentence/short-span verifier selection, downrank reused evidence across
  claims in the same verification batch, expose reuse diagnostics in metadata, and expand the
  evidence benchmark with chunk/span reuse and per-claim diversity diagnostics
- validation: focused verifier/service tests, full static/unit/frontend checks, live smoke, and
  live evidence benchmark

## 7. Implementation log

- 2026-05-01 / initial:
  - changes: created this ExecPlan for P1
  - rationale: P1 spans trust logic, API/report/frontend/docs, and validation; repository rules
    require an active plan
  - validation: pending code inspection
  - next: inspect scoring, verification, canonicalization, report, frontend, and benchmark paths
- 2026-05-01 / implementation:
  - changes: added deterministic source/chunk quality components, stricter canonicalization,
    candidate/weak/strong/contradict evidence relations, contradicted claim status, evidence
    ranking/diversity, citation precision metadata, API/report/frontend audit fields, docs, and
    `scripts/evidence_quality_benchmark.py`
  - rationale: keep P1 metadata-first and schema-free while making trust decisions auditable from
    existing ledger rows
  - validation: targeted ruff and 77 focused tests passed; full suite pending
  - next: run live smoke/benchmark and full validation suite
- 2026-05-02 / P1.5 closeout:
  - changes: scoped a metadata-only closeout for citation precision and evidence reuse; benchmark
    metrics will distinguish verified verifier evidence from draft `candidate_support` evidence so
    quality regressions are measured on the audited verifier output
  - rationale: the previous benchmark showed coarse precision and duplicate-content rates, but part
    of that signal came from draft candidate evidence that is not a verifier citation
  - validation: pending implementation and full command suite
  - next: implement verifier span selection, cross-claim reuse penalties, diagnostics, docs, tests,
    and live benchmark rerun
- 2026-05-02 / P1.5 implementation:
  - changes: verifier now considers short adjacent-sentence spans, ranks citation candidates by
    precision and specificity signals, selects a more restrained primary support/contradict
    evidence set, applies batch-local chunk/span/content reuse penalties, exposes reuse diagnostics
    in API quality payloads and report manifests, and expands the evidence benchmark with verified
    evidence metrics, top reused chunks/spans, and per-claim diversity diagnostics
  - rationale: improve the benchmark-visible citation precision and cross-claim evidence diversity
    without LLMs, schema changes, or pipeline rewrites
  - validation: full command suite, live smoke, and live evidence benchmark passed
  - next: keep deterministic verifier limitations documented; future semantic entailment remains
    deferred

## 8. Validation

- `python3 -m ruff check .` - passed
- `python3 -m black --check .` - passed
- `python3 -m pytest` - passed, 271 tests
- `cd apps/web && npm run build` - passed
- `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit` -
  passed
- `git diff --check` - passed
- live smoke:
  - command: `python3 scripts/smoke_planner_pipeline.py --query "What is SearXNG and how does it work?" --base-url http://127.0.0.1:8000 --wait-seconds 420`
  - result: passed, task `2b870ac2-964b-4a22-a261-b62e0d1ef979`, completed with
    `real-search+opensearch+no-LLM`
- evidence benchmark:
  - command: `python3 scripts/evidence_quality_benchmark.py --base-url http://127.0.0.1:8000 --wait-seconds 420 --json-output /tmp/deepsearch-evidence-benchmark.json --markdown-output /tmp/deepsearch-evidence-benchmark.md`
  - result: passed, 4/4 completed; aggregate average source quality `0.8075`, evidence per
    claim `2.1875`, citation precision `0.5114`, duplicate source rate `0.0`
- P1.5 validation:
  - `python3 -m ruff check .` - passed
  - `python3 -m black --check .` - passed
  - `python3 -m pytest` - passed, 273 tests
  - `cd apps/web && npm run build` - passed
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit` -
    passed
  - `git diff --check` - passed
  - live smoke:
    - command: `python3 scripts/smoke_planner_pipeline.py --query "What is SearXNG and how does it work?" --base-url http://127.0.0.1:8000 --wait-seconds 420`
    - result: passed, task `8634958a-66a1-4343-932e-3faad6f07d7c`, completed with
      `real-search+opensearch+no-LLM`
  - evidence benchmark:
    - command: `python3 scripts/evidence_quality_benchmark.py --base-url http://127.0.0.1:8000 --wait-seconds 420 --json-output /tmp/deepsearch-evidence-benchmark.json --markdown-output /tmp/deepsearch-evidence-benchmark.md`
    - result: passed, 4/4 completed; aggregate average source quality `0.8075`, evidence per
      claim `1.0625`, verified citation precision `1.0000`, duplicate source rate `0.0`,
      verified evidence content duplicate rate `0.0`, total chunk reuse `5`, total span reuse `0`

## 9. Risks and unknowns

- deterministic lexical verification can still miss paraphrase support and subtle contradiction
- stricter evidence thresholds may reduce supported-claim counts and expose weak coverage
- existing dirty worktree includes unrelated changes; avoid accidental revert or broad churn
- live benchmark depends on external search/fetch variability
- cross-claim evidence diversity uses small deterministic penalties and should not be interpreted
  as semantic reranking

## 10. Rollback / recovery

- revert P1 helper/service/API/frontend/docs/benchmark changes together
- because the planned approach is metadata-first, no database rollback should be needed unless a
  later milestone introduces a migration
- regenerated reports or benchmark artifacts can be discarded; ledger rows from live tests remain
  audit records

## 11. Deferred work

- LLM source judge shadow mode
- embeddings or semantic reranking
- durable source-quality model table
- richer cross-source contradiction reasoning
- browser/Tika extraction for non-HTML evidence
