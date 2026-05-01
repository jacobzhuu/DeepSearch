# Phase 2/3 Multiformat Acquisition and Intelligence MVP

## 1. Objective

Add a minimal, auditable P2/P3 increment that extends the existing deterministic research
pipeline to multiformat sources and introduces stable intelligence-layer entry points without
breaking the no-LLM alpha path.

## 2. Why this exists

The current pipeline can run host-local research end to end, but it is still centered on HTML and
plain text. Deep Research style work needs PDFs, Office attachments, richer retrieval diagnostics,
first-class research-plan visibility, gap/refinement observability, and optional LLM advisory
layers that fail closed.

## 3. Scope

### In scope

- PDF, DOCX, PPTX, and XLSX text extraction through standard-library parsers.
- MIME policy and parser metadata stored in existing source/chunk metadata and task events.
- Deterministic retrieval/rerank diagnostics for local/OpenSearch retrieval payloads.
- P2/P3 smoke benchmark scripts with JSON and Markdown outputs.
- A stable research-plan read API backed by existing task events.
- Optional LLM source judge in shadow mode only, with deterministic fallback.
- Documentation updates for the host-local route.

### Out of scope

- Browser-rendered fetch implementation; document as experimental/deferred.
- Recursive attachment crawling; document and benchmark the current no-recursion boundary.
- Embedding/vector search; keep deterministic lexical retrieval as the fallback path.
- New durable queue, auth, permission model, or distributed worker lease system.
- Large relational schema migration for research plans or parser history.

## 4. Constraints

- Deterministic no-LLM runs must remain the default and continue to pass tests.
- LLM source judge must be optional, disabled by default, and unable to override SSRF, MIME, or
  deterministic source-quality boundaries.
- No supported claim may be generated without persisted source/citation/evidence provenance.
- Parser failures must be auditable and isolated to the source being parsed.
- Security boundaries take priority over format coverage.
- Avoid new production dependencies in this MVP; use stdlib-based parsing and document precision
  limits.

## 5. Relevant files and systems

- `services/orchestrator/app/parsing/`
- `services/orchestrator/app/services/parsing.py`
- `services/orchestrator/app/indexing/backends.py`
- `services/orchestrator/app/services/indexing.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/api/routes/research_tasks.py`
- `services/orchestrator/app/api/schemas/research_tasks.py`
- `docs/architecture.md`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `scripts/`
- `tests/unit/orchestrator/`
- `services/orchestrator/tests/`

## 6. Milestones

### Milestone 1
- intent: add parser MIME policy and stdlib PDF/Office extraction.
- code changes: parser helpers, chunk locator metadata, parse diagnostics, unit/API tests.
- validation: parsing helper/service/API tests.

### Milestone 2
- intent: improve deterministic retrieval/rerank diagnostics.
- code changes: local/OpenSearch retrieval scoring metadata and tests.
- validation: indexing backend and indexing API tests.

### Milestone 3
- intent: expose minimum P3 intelligence-layer surfaces.
- code changes: research-plan read API, source-judge shadow diagnostics, docs/tests.
- validation: research task API and source judge tests.

### Milestone 4
- intent: add phase benchmarks and documentation.
- code changes: scripts and docs updates.
- validation: benchmark unit tests plus full validation command set where services are available.

## 7. Implementation log

- 2026-05-02: Created plan after reading required project docs and auditing current parser,
  acquisition, indexing, pipeline, task event, and docs structure. Chosen route is no migration:
  parser status, format locators, rerank diagnostics, research-plan, gap, and source-judge
  metadata fit the existing event/metadata seams for this MVP.
- 2026-05-02: Implemented stdlib PDF/DOCX/PPTX/XLSX extraction, parser/MIME metadata, chunk
  structure locators, source API parser metadata, deterministic retrieval rerank diagnostics,
  `GET /plan`, shadow LLM source judge diagnostics, phase2/phase3 benchmark scripts, frontend
  parser/source-judge display, and docs updates. Browser-rendered fetch, recursive attachments,
  embeddings, active LLM rerank, and separate research-plan/source-judge tables remain deferred.

## 8. Validation

- Required final checks:
  - `python3 -m ruff check .`
  - `python3 -m black --check .`
  - `python3 -m pytest`
  - `cd apps/web && npm run build`
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit`
  - `git diff --check`
- Live checks when backing services are available:
  - `python3 scripts/smoke_planner_pipeline.py --query "What is SearXNG and how does it work?" --base-url http://127.0.0.1:8000 --wait-seconds 420`
  - `python3 scripts/evidence_quality_benchmark.py --base-url http://127.0.0.1:8000 --wait-seconds 420 --json-output /tmp/deepsearch-evidence-benchmark.json --markdown-output /tmp/deepsearch-evidence-benchmark.md`
  - P2/P3 benchmark scripts added in this plan.

Completed narrow checks during implementation:

- `python3 -m pytest tests/unit/orchestrator/test_parsing_helpers.py tests/unit/orchestrator/test_parsing_service.py services/orchestrator/tests/test_parsing_api.py -q` — passed
- `python3 -m pytest tests/unit/orchestrator/test_indexing_backend.py services/orchestrator/tests/test_indexing_api.py -q` — passed
- `python3 -m pytest tests/unit/orchestrator/test_source_judge.py services/orchestrator/tests/test_research_tasks_api.py tests/unit/test_phase_benchmark_scripts.py -q` — passed

## 9. Risks and unknowns

- Stdlib PDF extraction is text-stream oriented and cannot match a full PDF engine.
- PDF page localization is best-effort; unreliable cases must carry fallback metadata.
- Office extraction is textual and does not execute macros or inspect embedded objects.
- Browser rendering remains deferred due to sandbox and deployment complexity.
- LLM source judge can misclassify sources; this milestone keeps it in shadow mode.

## 10. Rollback / recovery

No schema migration is planned. To roll back, revert the parser, retrieval, source-judge, API,
script, test, and docs changes. Existing HTML/plain text snapshots and task events remain valid.
If a parser bug affects a source, delete/reparse the affected `source_document`/`source_chunk`
rows for that task from a backup-aware maintenance session and rerun parsing/indexing.

## 11. Deferred work

- Dedicated parser-history table.
- Browser-rendered fetch worker with sandboxed Playwright/Chromium.
- Attachment parent/child relational model.
- Embedding or neural hybrid retrieval.
- Active LLM source reranking.
- LLM gap reasoner and contradiction judge.
- Full human review mutation workflow.
