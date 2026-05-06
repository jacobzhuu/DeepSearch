# Deployment Grounded Report Quality

## 1. Objective

Improve deployment-oriented research reports without changing the relational schema: preserve the selected report language through grounded LLM reporting, prioritize official deployment sources for SearXNG Docker tasks, extract command/config snippets as evidence-backed deployment records, and surface deployment coverage gaps when verified evidence is incomplete.

## 2. Why this exists

The real DeepSeek planner plus grounded report writer path can complete a Docker deployment task, but the resulting report may be low quality because the verified ledger contains only edge facts rather than deployment steps. Deployment questions need source selection and evidence extraction that recognize official Docker repositories, commands, compose examples, ports, volumes, configuration, security, troubleshooting, and maintenance. The report writer must still remain grounded in verified claim/evidence/citation-span rows.

## 3. Scope

### In scope

- Resolve and record report language consistently for deterministic and grounded LLM reports.
- Add deterministic SearXNG Docker known-path and official repository recognition.
- Add raw GitHub README, compose, and env source handling for deployment repositories.
- Parse raw Markdown/YAML/env content as safe text without executing content, preserving YAML/env
  indentation for evidence snippets.
- Add deployment answer slots for prerequisites, run/compose, volumes, ports, configuration, security, troubleshooting, and updates.
- Allow deployment command/config snippets to become evidence-backed claim records with explicit metadata.
- Add report assembly and prompt guidance so grounded reports organize deployment evidence, render
  code/config snippets as fenced Markdown blocks, and name coverage gaps.
- Keep deployment slot mapping precise: `docker exec ... root` is troubleshooting, `FORCE_OWNERSHIP`
  is volume/configuration, and only reverse proxy / limiter / secret / certificate / public-instance
  evidence satisfies security.
- Keep multiline shell/YAML/env fenced blocks complete through citation-span selection and report
  rendering.
- Update focused tests and operator docs.

### Out of scope

- No database migration or new relational deployment-step table.
- No ungrounded LLM command generation.
- No active LLM source judge, LLM gap reasoner, browser rendering, Tika, HTML/PDF export, or Docker-first packaging expansion.
- No broad verifier redesign beyond accepting command/config evidence that is already persisted and verified.
- No further source-discovery expansion beyond previously added stale-404 cleanup and raw source handling.

## 4. Constraints

- Phase 11/P2-P3 boundaries remain in force.
- The report must only render facts present in verified claims and citation excerpts.
- Code/config evidence must retain source_chunk, citation_span, and claim_evidence traceability.
- Existing tasks without new metadata must keep rendering through the backward-compatible path.
- No new production dependencies.
- SSRF/acquisition policy remains unchanged.
- Host-local/self-hosted operation remains the primary route.

## 5. Relevant files and systems

- `services/orchestrator/app/reporting/language.py`
- `services/orchestrator/app/reporting/grounded_llm.py`
- `services/orchestrator/app/reporting/markdown.py`
- `services/orchestrator/app/services/reporting.py`
- `services/orchestrator/app/research_quality/answer_slots.py`
- `services/orchestrator/app/research_quality/source_intent.py`
- `services/orchestrator/app/services/search_discovery.py`
- `services/orchestrator/app/parsing/quality.py`
- `services/orchestrator/app/claims/drafting.py`
- `services/orchestrator/app/services/claims.py`
- focused tests under `services/orchestrator/tests/` and `tests/unit/orchestrator/`
- docs: `docs/architecture.md`, `docs/api.md`, `docs/schema.md`, `docs/runbook.md`

## 6. Milestones

### Milestone 1
- intent: preserve report language and record the writer language unambiguously.
- code changes: normalize writer metadata and add task-event fallback language resolution if needed.
- validation: report synthesis tests covering `zh-CN` grounded LLM request metadata, prompt, manifest, and Markdown headings.

### Milestone 2
- intent: prioritize official SearXNG Docker deployment sources.
- code changes: classify `github.com/searxng/searxng-docker` and raw owned GitHub deployment files as official repository evidence; inject bounded raw README, compose, and env candidates while removing the stale `blob/master/docker-compose.yaml` path.
- validation: source-intent and search-discovery tests for official repo/raw README/raw compose/raw env candidates and stale-path exclusion.

### Milestone 3
- intent: allow command/config snippets to enter the evidence ledger for deployment tasks.
- code changes: extract deployment command/config spans from chunks, mark them as deployment evidence, avoid rejecting them as generic diagram/config fragments, and store slot/lineage metadata in claim notes.
- validation: claim helper/service tests for docker run/docker compose/config snippets and slot coverage.

### Milestone 4
- intent: assemble deployment reports around evidence-backed deployment slots and explicit coverage gaps.
- code changes: add deployment-specific labels/sections/prompt requirements and coverage-gap rendering based on verified claims/slot coverage.
- validation: deterministic and grounded report tests for deployment sections, Chinese language, and missing-command coverage gap.

## 7. Implementation log

- 2026-05-05 / session:
  - changes: created this ExecPlan after reproducing the relevant code paths from repository docs and implementation.
  - rationale: the fix spans source selection, extraction, report writing, docs, and tests, so a living plan is required.
  - validation: superseded by completed validation below.
  - next: implement milestones 1-4 in a narrow no-migration patch.
- 2026-05-05 / session:
  - changes: completed milestones 1-4 without a schema migration.
  - implementation:
    - normalized top-level `report_language` into task constraints and preserved selected report language through grounded LLM report writer prompts, metadata, manifest, and Markdown rendering.
    - added wrong-language validation for Chinese grounded LLM outputs, with deterministic Markdown fallback.
    - classified `github.com/searxng/searxng-docker` as `official_repository` for SearXNG Docker deployment queries and injected bounded SearXNG Docker known-path candidates.
    - expanded deployment answer slots and allowed deployment command/config snippets to become evidence-backed claims with `evidence_kind = "deployment_code_or_config"` and targeted `slot_ids`.
    - allowed deployment command/config citation excerpts through verification and report filtering only when backed by exact verified evidence.
    - rendered deployment slot sections and explicit coverage gaps in deterministic and grounded reports.
    - updated architecture, API, schema, runbook, and focused tests.
  - validation: focused pytest, ruff, and black checks passed; exact commands are recorded in the Validation section.
  - next: optional live DeepSeek/SearXNG run to inspect real-world source coverage and prose quality.
- 2026-05-05 / continuation:
  - changes: tightened the live-debugging leftovers without a schema migration.
  - implementation:
    - raw GitHub README candidates for repository pages are ranked before the HTML repository page so acquisition can fetch README text before GitHub UI chrome.
    - safe raw text MIME handling now includes Markdown/YAML/env, and YAML/env parsing preserves indentation for Compose/config snippets.
    - deployment slot drafting now distinguishes generic target-audience text from real implementation/security slots, treats `FORCE_OWNERSHIP` as volume/config evidence but not security by itself, extracts reverse proxy / limiter / certificates / settings / update / troubleshooting evidence, and maps archived/superseded repository caveats to update/maintenance.
    - deterministic and grounded deployment report assembly render command/config evidence as fenced code blocks with claim, claim-evidence, and citation ids.
  - validation: focused pytest, ruff, and black checks passed again; exact commands are recorded below.
  - next: optional live DeepSeek/SearXNG run to inspect real-world source coverage and prose quality.
- 2026-05-06 / continuation:
  - changes: shifted the remaining work away from search discovery and into claim drafting, slot mapping, citation span selection, and report rendering.
  - implementation:
    - deployment claim drafting now recognizes prerequisites, `compose pull`, reverse proxy, limiter, `settings.yml`, `SEARXNG_SECRET`, other `SEARXNG_*` env values, and custom certificates from parsed chunks.
    - security slot mapping is narrowed so only reverse proxy / limiter / bot protection / secret / certificate / public-instance exposure evidence satisfies security; `docker exec ... root` is troubleshooting only, and `FORCE_OWNERSHIP` remains volume/configuration only.
    - multiline shell/YAML/env fenced blocks can be drafted and verified as complete citation spans instead of being shortened to the first lines.
    - deterministic and grounded deployment evidence rendering now chooses the complete claim statement or persisted full evidence excerpt before falling back to support citation excerpt, so truncated citations do not become truncated code blocks.
  - validation: focused pytest passed; ruff/black validation is recorded below.
  - next: optional live DeepSeek/SearXNG run to inspect real-world source coverage and prose quality.
- 2026-05-06 / continuation:
  - changes: closed the remaining live-task coverage gap without expanding search or changing schema.
  - implementation:
    - deployment claim candidate selection now performs a slot-diverse pass before ordinary category diversification so evidence for prerequisites, run/compose, volumes, ports, configuration, security, troubleshooting, and maintenance can all survive claim limits when present in fetched chunks.
    - real/debug pipeline claim drafting and verification now use at least the deployment answer-slot count for deployment queries, bounded to the existing per-request caps, so the product path is not stuck with only five deployment claims.
    - deployment source chunks are ordered by official-source priority plus deployment evidence signals, letting chunks containing `Docker or Podman`, `sudo usermod -aG docker`, `settings.yml`, `.env`, `SEARXNG_*`, reverse proxy / limiter / certificate, `docker compose pull`, and logs/exec evidence reach drafting earlier.
    - deterministic report coverage counts now use verified slot coverage rows for deployment reports instead of broad category coverage, so missing deployment slots remain visible as coverage gaps.
  - validation: focused pytest, ruff, and black checks passed; exact commands are recorded below.
  - next: run the optional live SearXNG Docker task and inspect claims, claim evidence, report metadata, Chinese prose, and deployment slot gaps.
- 2026-05-06 / continuation:
  - changes: fixed the remaining source-chunk-to-verified-claim promotion gap without changing discovery, acquisition, or schema.
  - implementation:
    - deployment verification now falls back to existing `candidate_support` citation spans for deployment-slot claims when lexical retrieval misses the original source chunk.
    - the fallback is deployment-only and still reruns the strict verifier span matcher against the original `source_chunk` before creating `support` claim evidence.
    - deployment evidence detection now treats standalone `.env` and `update-ca-certificates` as deployment evidence markers.
    - added a focused source-chunk-to-claim-to-report regression that drafts from deployment `source_chunk` rows, verifies with an empty retrieval backend, and renders the Chinese grounded deterministic report with claim/evidence/citation traceability.
  - validation: focused pytest passed for the new regression and the existing deployment verifier test; full focused validation is pending in this session.
  - next: run focused pytest, ruff, black --check, and git diff --check.
- 2026-05-06 / continuation:
  - changes: closed the remaining grounded report coverage cap after verified deployment claims exist.
  - implementation:
    - deployment pipeline claim limits now allow four claims beyond the eight deployment answer slots, so fresh runs can carry separate prerequisite, configuration, security, maintenance, and troubleshooting evidence instead of stopping at one claim per slot.
    - grounded LLM deployment slot rendering no longer truncates each slot at four supported claims; every strong supported deployment-slot claim in the grounding bundle is rendered with claim, claim_evidence, and citation ids.
    - added a grounded LLM regression where source chunks produce more than eight supported deployment claims and the final zh-CN `llm_grounded` report renders Docker/Podman, `sudo usermod -aG docker`, `docker compose pull`, `settings.yml`, `.env`, `SEARXNG_*`, reverse proxy, limiter/bot protection, certificates / `update-ca-certificates`, the complete `docker run` block, and troubleshooting commands.
  - validation: targeted pytest passed for the claim-limit and grounded deployment report regressions; full focused validation is pending in this session.
  - next: run focused pytest, ruff, black --check, and git diff --check.
- 2026-05-06 / live acceptance loop:
  - changes: fixed the live source-chunk-to-claim drop point found by task `275080ce-4dd9-4d1f-b3bf-ba59a1dccb14`, where `sudo usermod -aG docker`, `docker compose pull`, `settings.yml`, `.env`, `SEARXNG_*`, reverse proxy, and certificate evidence existed in `source_chunks` but did not reach claims or the report.
  - implementation:
    - deployment drafting and verification now use a deployment-specific cap above the generic configured claim limit.
    - deployment candidate diversification now performs a marker-diverse pass after slot coverage so exact required snippets survive when present in parsed chunks.
    - docs now describe the deployment-specific cap and marker-diverse selection behavior.
  - validation:
    - fresh live task `ea2fdc97-e4f3-411c-acc7-f068010b988d` completed with real SearXNG search, OpenSearch, DeepSeek planner, and DeepSeek grounded report writer.
    - the live four-layer acceptance check passed for all required terms across `source_chunks`, `claims`, `claim_evidence`, and report Markdown, with no downstream gaps.
  - next: run focused pytest, ruff, black --check, and git diff --check after the live-pass patch.
- 2026-05-06 / reusable acceptance script:
  - changes: added `scripts/live_deployment_acceptance.py` as a reusable API-level live acceptance command for the SearXNG Docker deployment report path.
  - implementation:
    - the script creates a fresh zh-CN deployment task, queues the real worker pipeline, waits for a terminal status, exports `source_chunks`, `claims`, `claim_evidence`, report, events, and task detail, and evaluates the same four-layer deployment evidence contract used in the live debugging loop.
    - the runbook now documents the host-local command and artifact directory usage.
  - validation:
    - script syntax/help/lint/format checks passed.
    - a fresh script-created live task `ca475e0a-cc39-4e3b-905d-72c977af59fe` completed and the script returned `passed=true`.
  - next: use the script for future deployment-report live acceptance instead of ad hoc one-off snippets.

## 8. Validation

- Passed: `pytest services/orchestrator/tests/test_deployment_claim_quality.py tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_search_discovery_service.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_acquisition_service.py tests/unit/orchestrator/test_parsing_service.py tests/unit/orchestrator/test_report_synthesis_service.py services/orchestrator/tests/test_research_tasks_api.py -q`
- Passed: `pytest services/orchestrator/tests/test_deployment_claim_quality.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_report_synthesis_service.py tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_parsing_service.py tests/unit/orchestrator/test_acquisition_service.py tests/unit/orchestrator/test_search_discovery_service.py services/orchestrator/tests/test_research_tasks_api.py -q`
- Passed: `python -m ruff check services/orchestrator/app/api/routes/research_tasks.py services/orchestrator/app/claims/__init__.py services/orchestrator/app/claims/drafting.py services/orchestrator/app/claims/verification.py services/orchestrator/app/parsing/document_extractors.py services/orchestrator/app/parsing/extractors.py services/orchestrator/app/parsing/quality.py services/orchestrator/app/reporting/grounded_llm.py services/orchestrator/app/reporting/markdown.py services/orchestrator/app/research_quality/answer_slots.py services/orchestrator/app/research_quality/evidence.py services/orchestrator/app/research_quality/source_intent.py services/orchestrator/app/services/claims.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/services/parsing.py services/orchestrator/app/services/reporting.py services/orchestrator/app/services/search_discovery.py services/orchestrator/tests/test_deployment_claim_quality.py services/orchestrator/tests/test_research_tasks_api.py tests/unit/orchestrator/test_acquisition_service.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_parsing_service.py tests/unit/orchestrator/test_report_synthesis_service.py tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_search_discovery_service.py`
- Passed: `python -m black --check services/orchestrator/app/api/routes/research_tasks.py services/orchestrator/app/claims/__init__.py services/orchestrator/app/claims/drafting.py services/orchestrator/app/claims/verification.py services/orchestrator/app/parsing/document_extractors.py services/orchestrator/app/parsing/extractors.py services/orchestrator/app/parsing/quality.py services/orchestrator/app/reporting/grounded_llm.py services/orchestrator/app/reporting/markdown.py services/orchestrator/app/research_quality/answer_slots.py services/orchestrator/app/research_quality/evidence.py services/orchestrator/app/research_quality/source_intent.py services/orchestrator/app/services/claims.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/services/parsing.py services/orchestrator/app/services/reporting.py services/orchestrator/app/services/search_discovery.py services/orchestrator/tests/test_deployment_claim_quality.py services/orchestrator/tests/test_research_tasks_api.py tests/unit/orchestrator/test_acquisition_service.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_parsing_service.py tests/unit/orchestrator/test_report_synthesis_service.py tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_search_discovery_service.py`
- Passed: `pytest tests/unit/orchestrator/test_claim_drafting_service.py::test_claim_drafting_service_prioritizes_deployment_slot_coverage_with_product_limit tests/unit/orchestrator/test_claim_verification_service.py::test_claim_verification_service_supports_deployment_slot_evidence tests/unit/orchestrator/test_report_synthesis_service.py::test_deployment_report_renders_slot_sections_and_coverage_gaps -q`
- Passed: `pytest services/orchestrator/tests/test_deployment_claim_quality.py tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_search_discovery_service.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_claim_verification_service.py tests/unit/orchestrator/test_report_synthesis_service.py services/orchestrator/tests/test_research_tasks_api.py -q`
- Passed: `python -m ruff check services/orchestrator/app/services/claims.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/services/pipeline_runtime.py services/orchestrator/app/reporting/markdown.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_claim_verification_service.py tests/unit/orchestrator/test_report_synthesis_service.py`
- Passed: `python -m black --check services/orchestrator/app/services/claims.py services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/services/pipeline_runtime.py services/orchestrator/app/reporting/markdown.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_claim_verification_service.py tests/unit/orchestrator/test_report_synthesis_service.py`
- Passed: `pytest tests/unit/orchestrator/test_report_synthesis_service.py::test_deployment_source_chunks_promote_to_supported_claims_and_chinese_report tests/unit/orchestrator/test_claim_verification_service.py::test_claim_verification_service_supports_deployment_slot_evidence -q`
- Passed: `pytest services/orchestrator/tests/test_deployment_claim_quality.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_claim_verification_service.py tests/unit/orchestrator/test_report_synthesis_service.py -q`
- Passed: `python -m ruff check services/orchestrator/app/claims/drafting.py services/orchestrator/app/services/claims.py tests/unit/orchestrator/test_report_synthesis_service.py`
- Passed after formatting: `python -m black --check services/orchestrator/app/claims/drafting.py services/orchestrator/app/services/claims.py tests/unit/orchestrator/test_report_synthesis_service.py`
- Passed: `git diff --check`
- Passed: `python -m ruff check .`
- Passed: `python -m black --check .`
- Passed: `pytest tests/unit/orchestrator/test_report_synthesis_service.py::test_deployment_pipeline_claim_limit_allows_more_than_slot_count tests/unit/orchestrator/test_report_synthesis_service.py::test_grounded_deployment_report_renders_all_supported_slot_claims tests/unit/orchestrator/test_report_synthesis_service.py::test_deployment_source_chunks_promote_to_supported_claims_and_chinese_report -q`
- Passed: `pytest services/orchestrator/tests/test_deployment_claim_quality.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_claim_verification_service.py tests/unit/orchestrator/test_report_synthesis_service.py -q`
- Passed: `python -m ruff check services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/reporting/grounded_llm.py tests/unit/orchestrator/test_report_synthesis_service.py`
- Passed after formatting: `python -m black --check services/orchestrator/app/services/debug_pipeline.py services/orchestrator/app/reporting/grounded_llm.py tests/unit/orchestrator/test_report_synthesis_service.py`
- Passed: `git diff --check`
- Passed: `python -m ruff check .`
- Passed: `python -m black --check .`
- Passed: `pytest tests/unit/orchestrator/test_report_synthesis_service.py::test_deployment_pipeline_claim_limit_allows_more_than_slot_count tests/unit/orchestrator/test_claim_drafting_service.py::test_claim_drafting_service_prioritizes_deployment_slot_coverage_with_product_limit -q`
- Passed: `python -m ruff check services/orchestrator/app/services/claims.py services/orchestrator/app/services/debug_pipeline.py tests/unit/orchestrator/test_report_synthesis_service.py`
- Passed: `python -m black --check services/orchestrator/app/services/claims.py services/orchestrator/app/services/debug_pipeline.py tests/unit/orchestrator/test_report_synthesis_service.py`
- Passed live: restarted services with `DEV_ENV_FILE=.env.deepseek.local DEV_SKIP_FRONTEND=true DEV_BACKEND_RELOAD=false ./dev.sh restart`, created fresh task `ea2fdc97-e4f3-411c-acc7-f068010b988d` for `How to deploy SearXNG with Docker?` with `report_language=zh-CN` and `constraints.language=zh-CN`, waited for `COMPLETED`, exported `source_chunks`, `claims`, `claim_evidence`, and report Markdown, and confirmed every required deployment evidence term appeared in all four layers with `writer_mode=llm_grounded`.
- Passed: `pytest services/orchestrator/tests/test_deployment_claim_quality.py tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_claim_verification_service.py tests/unit/orchestrator/test_report_synthesis_service.py -q`
- Passed: `python -m ruff check services/orchestrator/app/services/claims.py services/orchestrator/app/services/debug_pipeline.py tests/unit/orchestrator/test_report_synthesis_service.py`
- Passed: `python -m black --check services/orchestrator/app/services/claims.py services/orchestrator/app/services/debug_pipeline.py tests/unit/orchestrator/test_report_synthesis_service.py`
- Passed: `git diff --check`
- Passed: `python -m py_compile scripts/live_deployment_acceptance.py`
- Passed: `python scripts/live_deployment_acceptance.py --help`
- Passed: `python -m ruff check scripts/live_deployment_acceptance.py`
- Passed: `python -m black --check scripts/live_deployment_acceptance.py`
- Passed live: `python scripts/live_deployment_acceptance.py --base-url http://127.0.0.1:8000 --artifact-dir /tmp/deepsearch-live-deployment-acceptance-script-final` created fresh task `ca475e0a-cc39-4e3b-905d-72c977af59fe` and returned `passed=true`.

## 9. Risks and unknowns

- Some websites may not expose compose examples in parseable HTML without browser rendering; the report must show a coverage gap instead of inventing commands.
- Lexical verification may still mark command snippets weak if retrieval does not find the exact snippet again; command claims should be drafted from precise citation spans to keep support recoverable.
- The current no-schema path stores deployment-step metadata in existing claim notes, which is acceptable for this increment but not a long-term replacement for a dedicated deployment-step ledger if the product grows.

## 10. Rollback / recovery

- Revert the touched source, tests, docs, and this plan file.
- Disable grounded report prose with `LLM_REPORT_WRITER_ENABLED=false` to return to deterministic Markdown.
- Existing report artifacts remain immutable historical outputs; regenerate a new report artifact version after rollback if needed.

## 11. Deferred work

- Dedicated `deployment_step` relational entity with typed command/config fields.
- Browser-rendered extraction for pages whose examples are hidden behind client-side rendering.
- Active LLM source judge or LLM gap reasoner.
- HTML/PDF report export.
