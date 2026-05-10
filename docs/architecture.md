# Architecture

## Current phase

This repository is in Phase 11 plus the P1/P1.5 evidence-credibility increment. The service still exposes the Phase 2 thin research task API, the Phase 3 search-discovery slice, the Phase 4 acquisition slice, the Phase 5 parsing and chunking slice, the Phase 6 indexing and retrieval slice, the Phase 7 claim-drafting slice, the Phase 8 verification slice, and the Phase 9 Markdown report-synthesis slice, while Phase 10 added the first real-infrastructure hardening layer for PostgreSQL, OpenSearch, MinIO, JSON logs, metrics, and report-artifact provenance metadata. The current route is no longer “ship a repo that anyone can immediately reproduce end to end”; it is a single-operator, host-local / self-hosted Linux research platform with optional Docker / compose packaging. Search discovery remains ledger-first and database-backed, acquisition can create `fetch_job` and `fetch_attempt` rows and persist raw response bytes into `content_snapshot`, parsing can read stored snapshots and persist provenance-linked `source_document` plus `source_chunk` rows with deterministic quality scoring, indexing can write task-scoped `source_chunk` records into a backend seam with a minimal OpenSearch implementation or an explicit development-only local backend, claim drafting creates candidate claims plus `candidate_support` citation bindings from retrieved or explicitly selected chunks, verification reuses retrieval to rank a small diverse evidence set and attach `support`, `weak_support`, or `contradict` evidence, now preferring precise sentence/short adjacent-sentence citations and applying small cross-claim chunk/span reuse penalties, and reporting can synthesize a Markdown artifact from the persisted claim and evidence ledger while persisting a hash plus manifest snapshot. Product `POST /api/v1/research/tasks/{task_id}/run` queues work by setting `research_task.status = QUEUED`; a host-local worker executes the same core pipeline runner used by the synchronous debug endpoint, writing stage progress plus checkpoints into `task_event` and `research_run.checkpoint_json`. The debug endpoint remains a synchronous development path, not the product execution path. The frontend now provides a task list, task creation with default `zh-CN` report language, pre-run research-plan generation/editing, queued run start, task detail status, Run/Pause/Resume/Cancel controls, polling task events, and minimal source/claim audit fields while the worker runs. Optional Research Planner v1 can run before search through a deterministic fallback, noop provider, or OpenAI-compatible LLM provider, but it only produces subquestions and search queries. LLM planner output is accepted only when it is a direct JSON object or one safely extractable fenced JSON object that validates against the strict planner JSON schema; unfenced prose around JSON, invalid JSON, schema failures, invalid source-type enums, provider errors, timeouts, disabled flags, or missing provider configuration produce a deterministic fallback plan with persisted planner status/source diagnostics. Planner diagnostics include parse-stage flags, sanitized raw-output previews, raw-output hashes, and categorized validation errors, and task detail preserves those diagnostics after operator edits. Final reporting defaults to deterministic Markdown, and can optionally use a grounded LLM report writer when `LLM_REPORT_WRITER_ENABLED=true`; that writer receives only verified claim/evidence/citation-span bundles. Main report narratives avoid internal claim/evidence/citation ids by default, with the ledger debug appendix available only when explicitly enabled. Source quality scoring remains deterministic first, with optional LLM source judging recorded as bounded advisory metadata unless active reranking is enabled.

Planner-enabled runs now include a deterministic research-quality layer around the LLM plan. The LLM may suggest subquestions, search queries, and source intent hints only; deterministic guardrails classify overview/definition queries, preserve original/official/about/Wikipedia/GitHub README searches, override Wikipedia avoid domains for overview references, add SearXNG known-path candidates when official SearXNG results are present, downrank admin/install/API/dev sources unless requested, measure answer-focused yield per source, and run a bounded supplemental acquisition pass when claim coverage is empty or too thin. Standalone search discovery can also run a deterministic authoritative-source resolver before generic provider results for recognized technical projects and research concepts, persisting official docs, project homepages, upstream repositories, package registries, reference pages, and academic/tutorial candidates as ordinary `search_query`/`candidate_url` ledger rows with `candidate_source=authoritative_source_resolver`; the worker/debug pipeline keeps that resolver disabled inside the main `SEARCHING` loop so resolver candidates do not consume the initial fetch budget or repeat during gap rounds. For LangGraph overview queries, accepted LLM plans are also merged with owned-source guardrail queries for `docs.langchain.com`, `reference.langchain.com`, `www.langchain.com/langgraph`, and `github.com/langchain-ai/langgraph`; LLM preferred domains can only supplement those owned preferences, broad `github.com/langchain-ai` preferences are supplemented with the concrete LangGraph repo path, and weak domains such as `langchain-ai.github.io` or `blog.langchain.dev` are marked secondary/downweighted in planner diagnostics. If main `SEARCHING` receives `searxng_empty_results_with_unresponsive_engines` before any candidates are available, known technical projects may inject bounded deterministic known-path candidates instead of failing immediately; LangGraph injects Python and JavaScript docs overview pages, reference docs, state-graph reference docs, upstream GitHub, and the official product page, with candidate metadata recording `candidate_source=known_path_fallback`, the fallback reason, and the original provider. Search provider timeouts and request failures are converted to structured `SearchProviderError` diagnostics, and off-entity specialty-engine results are filtered before acquisition with selected/rejected counts in search diagnostics. After verification, the gap analyzer inspects required answer-slot coverage; when required slots are missing or weak, it generates bounded supplemental search queries and the runner appends another search/fetch/parse/index/draft/verify round before reporting. Supplemental gap queries use deterministic per-slot variants so later rounds can try a different official/reference phrasing instead of stopping only because the first query already exists. If supplemental search only returns duplicate URLs, the gap round can still attempt existing unattempted high-value candidates before re-parsing, re-indexing, drafting, and verifying; if supplemental search itself is unavailable but existing source documents/chunks/claims can support a partial report, the runner records `gap_search_unavailable` / `supplemental_search_failed` warnings and continues to reporting instead of failing the task. `services/orchestrator/app/research_quality/` now centralizes shared answer-slot, source-intent, evidence-candidate, source-yield, evidence-yield, dropped-source reason, slot-coverage, and gap-analysis contracts so acquisition, pipeline diagnostics, answer-yield metrics, report manifests, and the frontend consume the same deterministic vocabulary. Claim drafting, source-yield diagnostics, verification, and report filtering share deterministic answer-role and evidence-lineage rules so navigation, project-meta, setup, diagram/config, generic documentation-pointer text, and weak lexical verifier matches do not enter the main answer as strongly supported facts.

Comparison source selection keeps all extracted entities in the deterministic ownership model and interleaves authoritative acquisition candidates by entity, so one project's official docs do not consume all early fetch slots when the query asks to compare two systems.

For technical library or framework concept queries, source-intent classification is query-subject aware: generic tutorial pages with titles such as "What is X" are no longer treated as official/about sources unless the domain/path context is owned by or strongly tied to the queried project. LangGraph currently treats `docs.langchain.com`, `reference.langchain.com`, `langchain.com`, and `github.com/langchain-ai/langgraph` as owned high-value sources; localized mirrors such as `github.langchain.ac.cn`, `langgraph.com.cn`, and `langchain-doc.cn` remain secondary references. GitHub repository candidates are official only when the owner/repo matches the known upstream project, so third-party tutorial repositories do not get upstream README priority. The LangGraph product page remains a valid owned source, but it is ranked behind docs/reference/upstream GitHub for how-it-works overview acquisition so it does not crowd out implementation evidence. Job boards, freelance listings, SEO repost pages, and obvious listing URLs are treated as low quality for overview queries. The no-LLM planner and claim scorer now use generic framework mechanism terms such as state, graph, nodes, edges, workflow, orchestration, routing, durable execution, streaming, memory, checkpointing, human-in-the-loop, integrations, APIs, and limitations instead of SearXNG-specific metasearch terms for non-SearXNG subjects. Gap rounds ignore newly discovered low-value candidates before falling back to already discovered unattempted high-value candidates, and LangGraph gap searches add bounded owned-source queries for LangChain docs, reference docs, and the `langchain-ai/langgraph` repository.

The LLM-assisted quality layer is documented in `plans/llm-assisted-source-judge-and-planner.md`.
It preserves the current deterministic pipeline as the authority of record. DeepSeek is used only
through the existing OpenAI-compatible provider path; it is not a search provider and cannot bypass
`candidate_url`, `fetch_attempt`, `content_snapshot`, `source_document`, `source_chunk`,
`citation_span`, `claim`, `claim_evidence`, or `report_artifact`. Optional LLM stages now cover
query rewriting, source judging, evidence reranking, claim review, and grounded report writing.
Each stage has an independent enablement flag, bounded input/output size, structured JSON
validation, diagnostics, and deterministic fallback. The query rewriter, evidence reranker, and
claim reviewer normalize common provider JSON aliases before strict validation, but failed
normalization remains visible in task diagnostics. Claim-candidate scoring treats low
quality/answer/relevance scores as ranking and LLM-review inputs rather than fatal filters; only
clear non-evidence, unsafe/ineligible chunks, and non-claimable text are hard rejected before
selection. Source judging may actively rerank only behind
`LLM_SOURCE_JUDGE_ACTIVE_RERANK=true`, and even then deterministic ownership, low-value source
filters, blocklists, SSRF/acquisition policy, and official-source priority remain final; active
participation counts and guardrail reasons are exposed in observability. Evidence reranking can rank
only existing chunk ids, claim review can review only existing draft claim ids, and report writing
can render only validated claim/evidence/citation ids. Task-detail observability keeps pipeline
evidence/source-yield summaries stable after report generation instead of replacing them with the
smaller report-manifest subset.

The iterative research-loop optimization adds an optional `LLMResearchStrategist` in
`services/orchestrator/app/research_quality/llm_research_strategist.py` plus a deterministic
coverage evaluator. The strategist receives only a compact task-state summary after verification:
question, prior queries, remaining budgets, candidate summaries, verified claim summaries, and
slot coverage. It returns structured stop/continue decisions and next search queries. By default
this is shadow diagnostics only. When `RESEARCH_LOOP_ENABLED=true`,
`RESEARCH_LOOP_STRATEGIST_ENABLED=true`, and `RESEARCH_LOOP_STRATEGIST_SHADOW_MODE=false`, valid
`continue_search` strategist queries can replace deterministic gap-analyzer supplemental queries;
invalid output, provider failure, disabled flags, or empty query lists fall back to
`gap_analyzer.py`. The strategist never creates claims, evidence, sources, or reports directly.
Structured source judging now also records source triage fields, and active triage can attempt
`must_fetch` candidates before generic ranked candidates while skipping explicit low-value or
duplicate LLM-triaged candidates. The existing acquisition policy, canonical URL ledger, fetch
attempt recording, MIME policy, and deterministic fallback remain the authority of record.

### Deployment report quality increment

Deployment-oriented tasks keep the same ledger-first path but use a specialized deterministic
contract. SearXNG Docker deployment queries can inject bounded known-path candidates for the
official installation page, the `github.com/searxng/searxng-docker` repository, raw GitHub
README candidates for repository pages, and raw compose/env examples. Raw `README.md`, YAML, and
env files are parsed as safe text, with YAML/env indentation preserved for command/config
evidence. The `searxng/searxng-docker` repository is classified as `official_repository`, while
its archived/superseded status can enter the report as a limitation or maintenance caveat.
Deployment answer slots cover prerequisites, Docker run/compose, volumes, ports, configuration,
security, troubleshooting, and update/maintenance.

Claim drafting normally rejects diagram/config fragments, but deployment queries may promote
Docker commands, compose YAML, port mappings, volume mounts, prerequisites, `settings.yml`,
`SEARXNG_*` environment values, reverse-proxy / limiter / secret / certificate guidance,
troubleshooting text, and maintenance commands into evidence-backed claim records. Multiline
shell/YAML/env fenced blocks are kept as complete citation spans when possible. Deployment claim
selection uses a deployment-specific cap plus slot- and marker-diverse selection, so broad slot
coverage does not crowd out exact snippets such as `sudo usermod -aG docker`, `docker compose pull`,
`.env`, `SEARXNG_*`, reverse proxy, limiter/bot protection, certificates, and troubleshooting
commands when those snippets are present in parsed chunks. Security coverage is intentionally
narrow: reverse proxy, limiter/bot protection, secrets, certificates, and public instance exposure
can satisfy the security slot; `docker exec ... root` is troubleshooting, and `FORCE_OWNERSHIP` is
volume/configuration evidence only. Those records still bind to exact `citation_span` excerpts and
verified `claim_evidence`; the no-schema metadata lives in existing JSON fields such as
`source_chunk.metadata_json` and `claim.notes_json`.

The grounded LLM report writer receives the resolved report language in both metadata and the
grounding bundle. Chinese requests are validated for Chinese output before rendering; English-only
LLM payloads for `zh-CN` requests fall back to deterministic Markdown. Deployment reports render
slot-organized evidence, fenced code/config blocks with claim/evidence/citation traceability, and
explicit coverage gaps when the verified ledger lacks a command or configuration snippet for a
required slot. For deployment code/config records, rendering prefers the complete claim statement
or persisted full evidence excerpt over a shortened citation excerpt.

## Layer boundaries

- UI / gateway layer: `apps/web/` now provides task listing, task creation, pre-run plan confirmation, queued worker start, status-aware Run/Pause/Resume/Cancel controls, polling task detail progress/events, source, claim, and Markdown report views
- orchestrator / workflow layer: `services/orchestrator/app/` now contains the thin research task API, request and response schemas, database dependencies, the task service layer, the host-local worker, the Phase 3 search-discovery seams, the Phase 4 acquisition service, the Phase 5 parsing service, the Phase 6 indexing service, the Phase 7 plus Phase 8 claims service, and the Phase 9 plus Phase 10 report-synthesis and deploy-hardening services
- persistence / ledger layer: `migrations/` and `packages/db/` hold the schema, ORM, session helpers, and repositories
- acquisition / parsing / indexing layer: the codebase now includes a minimal search-provider abstraction, a SearXNG-backed implementation, a policy-guarded HTTP acquisition client, a filesystem-backed and MinIO-backed snapshot storage seam, a minimal parser and chunker for `text/html`, `text/plain`, and safe raw text formats such as Markdown/YAML/env, MediaWiki/Wikipedia article-body extraction with paragraph fallback metadata, deterministic source/chunk quality scoring, a chunk-index backend seam with a live-validatable OpenSearch REST implementation plus task-scoped retrieval, and deterministic claim-drafting plus verification helpers for candidate citation binding, weak/strong support, contradiction scanning, and evidence ranking; browser, Tika, embeddings, and semantic reranking remain placeholders
- reporting / delivery layer: a minimal Markdown report synthesis path now exists inside orchestrator, while dedicated report service and export formats remain placeholders
- observability layer: `packages/observability/` now provides JSON-log configuration, request metrics, and key task/fetch/parse/verify/report counters

## Repository shape

- `services/orchestrator/`: runnable FastAPI service skeleton for future research task APIs
- `services/orchestrator/app/services/`: thin task state transition logic plus queueing, worker execution, Phase 3 search discovery, Phase 4 acquisition orchestration, Phase 5 parsing orchestration, Phase 6 indexing orchestration, Phase 7 plus Phase 8 claims orchestration, and Phase 9 report synthesis orchestration
- `services/orchestrator/app/search/`: provider abstraction, SearXNG client, optional YaCy client, query expansion, authoritative-source resolver, and URL canonicalization helpers
- `services/orchestrator/app/llm/`: optional noop and OpenAI-compatible provider seam for planner-only use
- `services/orchestrator/app/planning/`: Research Planner v1 dataclasses, JSON parsing, deterministic fallback, and planner service
- `services/orchestrator/app/acquisition/`: HTTP acquisition policy and fetch client
- `services/orchestrator/app/parsing/`: minimal HTML and plain-text extraction plus stable chunking helpers
- `services/orchestrator/app/storage/`: snapshot and artifact object-store interface plus filesystem and MinIO backends
- `services/orchestrator/app/indexing/`: chunk-index backend abstraction plus the minimal OpenSearch REST implementation with Phase 10 validation and error wrapping
- `services/orchestrator/app/research_quality/`: shared deterministic source-intent classification, answer-slot coverage, evidence-candidate, source-yield, evidence-yield, dropped-source reason, slot-coverage contracts, source judging, and LLM-assisted query/evidence/claim quality services used across selection, diagnostics, verification, and reporting
- `services/orchestrator/app/claims/`: deterministic Phase 7 and Phase 8 helpers for claim sentence selection, deployment command/config evidence extraction, confidence heuristics, citation span validation, and minimal verification conflict handling
- `services/orchestrator/app/reporting/`: deterministic Phase 9 Markdown report rendering, deployment slot coverage/gap rendering, optional grounded LLM report writing, report-language helpers, and Phase 10 manifest helpers
- `packages/db/`: SQLAlchemy models, session helpers, and repository skeletons for the research ledger
- `packages/observability/`: JSON logging and metrics helpers
- `migrations/`: Alembic environment and the initial reversible schema migration
- `scripts/`: host-local operational helpers for migration, bucket initialization, index initialization, mock SearXNG, and end-to-end smoke validation
- `services/crawler/`, `services/reporter/`, `services/openclaw/`: directory placeholders only
- `packages/`: reserved for shared packages introduced in later phases
- `infra/`: incremental infrastructure configuration and deployment-side inputs such as OpenSearch CA material
- `docs/phases/phase-0.md`: current phase scope and deliverables
- `docs/phases/phase-1.md`: current schema-phase scope and deliverables
- `docs/phases/phase-2.md`: task API and event-stream scope and deliverables
- `docs/phases/phase-3.md`: search discovery and candidate URL intake scope and deliverables
- `docs/phases/phase-4.md`: HTTP acquisition and content snapshot scope and deliverables
- `docs/phases/phase-5.md`: parsing and source-chunk scope and deliverables
- `docs/phases/phase-6.md`: indexing and retrieval scope and deliverables
- `docs/phases/phase-7.md`: claim drafting and citation binding scope and deliverables
- `docs/phases/phase-8.md`: verification and conflict handling scope and deliverables
- `docs/phases/phase-9.md`: Markdown report synthesis scope and deliverables
- `docs/phases/phase-10.md`: real infrastructure validation, observability, and report artifact hardening scope and deliverables
- `docs/phases/phase-11.md`: deployment packaging, compose wiring, init scripts, and smoke validation scope and deliverables

## Phase 11 design constraints

- keep the product centered on `research_task`; product execution starts by queueing the task and a host-local worker advances runtime statuses
- keep the task API semantics from Phase 2 intact while adding only the minimal search discovery, acquisition, and parsing endpoints
- keep `resume` explicit: paused work returns to `QUEUED` and is picked up by the host-local worker
- keep search discovery, acquisition, and parsing bounded; product execution is worker-driven, while the debug endpoint remains synchronous for development diagnostics
- canonicalize URLs before task-scoped dedupe and allow or deny filtering
- keep acquisition policy explicit: only `http` and `https`, no loopback or private targets, bounded timeouts, bounded redirects, and bounded response sizes
- keep parsing and chunking explicit: `text/html`, `text/plain`, and safe raw text formats such as Markdown/YAML/env are parsed without executing remote code; HTML uses minimal body extraction plus MediaWiki paragraph fallback when strict article extraction would be empty, and chunking uses a stable paragraph-window chunker
- keep indexing and retrieval explicit: deterministic `source_chunk_id` traceability, task-scoped filtering, simple match retrieval, and thin debug APIs only
- keep claim drafting explicit: candidate-only evidence binding, draft-only verification status, deterministic query-aware sentence scoring/selection, conservative explanatory fallback only after strict filters produce no claims, no-claims diagnostics in pipeline failure details, and exact offset plus excerpt validation against `source_chunk.text`
- keep deterministic claim quality filters conservative: skip short fragments, title/question-like statements, figure captions, diagram/config fragments, incomplete sentences, and case/punctuation duplicates before claim persistence or report rendering
- keep optional planner output bounded by deterministic guardrails: for definition or overview queries, stable reference domains such as Wikipedia are not treated as hard avoids, original/user query plus official/about/Wikipedia/GitHub README guardrail searches are retained, SearXNG overview runs add deterministic known-path candidates for `docs.searxng.org/user/about.html` and `en.wikipedia.org/wiki/SearXNG` when official SearXNG results are present, and official about/reference sources outrank admin architecture, installation, API, or developer pages unless the user asks for those topics
- keep planned LLM-assisted source judgment advisory-first: shadow mode must not change ranking; active mode must be separately flagged, bounded, fully audited, and unable to override ownership evidence, blocklists, low-value source filters, SSRF/acquisition policy, or official-source deterministic priority
- keep answer-yield recovery deterministic: if claim drafting creates no claims, or creates only insufficient coverage for a what/how query, one bounded supplemental acquisition pass may fetch 1-3 unattempted high-value sources before parsing, indexing, and drafting are retried once
- keep gap recovery deterministic: after verification, required answer slots that are missing or weak may trigger up to `RESEARCH_GAP_MAX_ROUNDS` supplemental search/fetch/parse/index/draft/verify rounds before reporting
- keep technical concept source selection deterministic: title-only generic tutorials must not be promoted to official/about, official docs/reference/GitHub candidates should be attempted before generic articles when the query subject matches owned project metadata, localized mirrors should remain secondary unless explicitly whitelisted as owned, third-party GitHub tutorials should not receive upstream repository priority, and job/freelance/listing pages should stay low quality for overview queries
- keep answer slots explicit and deterministic: planning and reporting may expose query-specific slots, but slot coverage must be derived from persisted claim categories and evidence-backed claims rather than LLM-written facts
- keep deployment answers evidence-first: deployment reports should organize prerequisites, Docker run/compose, volumes, ports, configuration, security, troubleshooting, and update/maintenance from verified command/config evidence, and must show coverage gaps instead of inventing missing commands
- keep verification explicit: candidate evidence is not verified support, weak lexical support is not promoted to support, selected evidence is ranked and diversified deterministically, and the stable verification statuses are `draft`, `supported`, `mixed`, `contradicted`, and `unsupported`
- keep reporting explicit: Markdown only, evidence-first synthesis only, no claim without persisted evidence, no low-quality/off-query claim promoted solely because it is marked `supported`, and optional LLM report prose must be grounded by validated claim/evidence/citation ids
- keep provider, acquisition, parser, object-store, worker, and index seams minimal but extensible for later leases, browser-fetch, Tika, and richer OpenSearch work
- keep migration, ORM, repository, and service behavior aligned; Phase 5 only adds the minimum snapshot-provenance link required for `source_document`
- keep snapshot and index backend misconfiguration as an application-startup failure rather than a first-request failure
- keep OpenSearch live validation opt-in and explicit so unit-test startup is not coupled to a running cluster
- keep report artifact integrity explicit through stored content hashes and manifest snapshots
- keep observability additive; worker, checkpoint, and gap-analysis events must remain inspectable through task events
- keep host-local and self-hosted Linux operation as the primary operator path
- keep deployment packaging explicit but optional:
  - compose may remain in-repo as optional tooling
  - compose validation is not the primary acceptance gate
  - bucket, index, migration, and smoke initialization stay as explicit operator steps or scripts
- keep repository and service code free of browser fallback, Tika, HTML/PDF export, and LLM-authored ungrounded gap analysis
- keep health and readiness endpoints free of external dependency checks until backing services are introduced

## Current operational profile

- recommended runtime path:
  - Python environment on a Linux host
  - PostgreSQL
  - MinIO or the filesystem object-store backend
  - OpenSearch
  - orchestrator process
  - host-local research worker process (`python scripts/research_worker.py`)
- optional tooling:
  - `docker-compose.yml`
  - `docker-compose.dev.yml`
- current closed loop:
  - `task -> queued -> worker -> search -> fetch -> parse -> index -> draft -> verify -> optional gap rounds -> report`
- current generalization benchmark:
  - `python scripts/benchmark_queries.py --json` lists the minimum multi-query benchmark set
  - `python scripts/benchmark_queries.py --run --base-url http://127.0.0.1:8000 --json` runs it against a live orchestrator when backing services are available
  - `SEARCH_PROVIDER=smoke` plus `INDEX_BACKEND=local` uses synthetic `deepsearch-smoke.local` fixtures and network-free smoke acquisition for deterministic development completion checks; it is not real search evidence
- intentionally not expanded further in the current route:
  - OpenClaw
  - HTML/PDF export
  - distributed queue / worker leases
  - complex verifier semantics
  - complex retrieval optimization

## P2/P3 multiformat and intelligence MVP

The P2/P3 increment keeps the existing deterministic alpha path intact and adds no relational
schema migration. PDF, DOCX, PPTX, and XLSX parsing now enter the same
`content_snapshot -> source_document -> source_chunk -> index -> claim/evidence/report` path as
HTML and plain text. Parser status, MIME policy, source format, text length, parser warnings, page
range, slide range, sheet name, cell range, and locator fallback reasons are stored in existing
`source_chunk.metadata_json` and surfaced through parse decisions and source APIs. The parsers use
standard-library text extraction only; they do not execute Office macros, scripts, external
resources, or embedded objects. PDF page localization is best-effort and explicitly records
`pdf_page_stream_mapping_unreliable` when stream-to-page mapping is not trustworthy.

Retrieval remains deterministic lexical retrieval with an explainable quality rerank layer. The
index response metadata now includes `retrieval_diagnostics` with lexical score, source quality,
chunk quality, information density, freshness, citation precision likelihood, diversity penalty,
and final rerank score. This is a BM25/local lexical enhancement, not embedding search. Embedding
and neural hybrid search remain deferred so missing model/API configuration cannot break the
host-local no-LLM route.

P3 adds a stable `GET /api/v1/research/tasks/{task_id}/plan` read surface over the existing
`research_plan.created` event contract. Optional LLM source judging records model/provider, prompt
version, input summary, structured judgment, confidence, reasons, fallback status, and whether a
bounded active-rerank adjustment was used. It remains shadow-only unless
`LLM_SOURCE_JUDGE_ACTIVE_RERANK=true`, and even active mode cannot override deterministic source
quality guardrails, SSRF, MIME policy, blocklists, low-value filters, or official-source priority.
Browser-rendered fetch and recursive attachment crawling remain deferred/experimental because they
require a stronger sandbox and parent-child source model than this no-migration MVP.
