# Architecture

## Current phase

This repository is in Phase 11. The service still exposes the Phase 2 thin research task API, the Phase 3 search-discovery slice, the Phase 4 acquisition slice, the Phase 5 parsing and chunking slice, the Phase 6 indexing and retrieval slice, the Phase 7 claim-drafting slice, the Phase 8 verification slice, and the Phase 9 Markdown report-synthesis slice, while Phase 10 added the first real-infrastructure hardening layer for PostgreSQL, OpenSearch, MinIO, JSON logs, metrics, and report-artifact provenance metadata. The current route is no longer “ship a repo that anyone can immediately reproduce end to end”; it is a single-operator, host-local / self-hosted Linux research platform with optional Docker / compose packaging. Search discovery remains ledger-first and database-backed, acquisition can create `fetch_job` and `fetch_attempt` rows and persist raw response bytes into `content_snapshot`, parsing can read stored snapshots and persist provenance-linked `source_document` plus `source_chunk` rows, indexing can write task-scoped `source_chunk` records into a backend seam with a minimal OpenSearch implementation or an explicit development-only local backend, claim drafting can draft support-only claims plus citation bindings from retrieved or explicitly selected chunks, verification can reuse retrieval to attach support or contradict evidence and resolve each claim into a minimal verification status, and reporting can synthesize a Markdown artifact from the persisted claim and evidence ledger while persisting a hash plus manifest snapshot. Product `POST /api/v1/research/tasks/{task_id}/run` now queues work by setting `research_task.status = QUEUED`; a host-local worker executes the long-running pipeline and writes stage progress plus checkpoints into `task_event` and `research_run.checkpoint_json`. The debug endpoint remains a synchronous development path. The frontend creates a task with a default `zh-CN` report language, generates or edits a pre-run research plan, confirms that plan, queues the task, then polls task detail/events while the worker runs. Optional Research Planner v1 can run before search through a deterministic fallback, noop provider, or OpenAI-compatible LLM provider, but it only produces subquestions and search queries. LLM planner output is accepted only when it is a direct JSON object or one safely extractable fenced JSON object that validates against the strict planner JSON schema; unfenced prose around JSON, invalid JSON, schema failures, invalid source-type enums, provider errors, timeouts, disabled flags, or missing provider configuration produce a deterministic fallback plan with persisted planner status/source diagnostics. Planner diagnostics include parse-stage flags, sanitized raw-output previews, raw-output hashes, and categorized validation errors, and task detail preserves those diagnostics after operator edits. Final reporting defaults to deterministic Markdown, and can optionally use a grounded LLM report writer when `LLM_REPORT_WRITER_ENABLED=true`; that writer receives only verified claim/evidence/citation-span bundles and the rendered artifact still includes claim/evidence id mappings.

Planner-enabled runs now include a deterministic research-quality layer around the LLM plan. The LLM may suggest subquestions, search queries, and source intent hints only; deterministic guardrails classify overview/definition queries, preserve original/official/about/Wikipedia/GitHub README searches, override Wikipedia avoid domains for overview references, add SearXNG known-path candidates when official SearXNG results are present, downrank admin/install/API/dev sources unless requested, measure answer-focused yield per source, and run a bounded supplemental acquisition pass when claim coverage is empty or too thin. For LangGraph overview queries, accepted LLM plans are also merged with owned-source guardrail queries for `docs.langchain.com`, `reference.langchain.com`, `www.langchain.com/langgraph`, and `github.com/langchain-ai/langgraph`; LLM preferred domains can only supplement those owned preferences, broad `github.com/langchain-ai` preferences are supplemented with the concrete LangGraph repo path, and weak domains such as `langchain-ai.github.io` or `blog.langchain.dev` are marked secondary/downweighted in planner diagnostics. If main `SEARCHING` receives `searxng_empty_results_with_unresponsive_engines` before any candidates are available, known technical projects may inject bounded deterministic known-path candidates instead of failing immediately; LangGraph injects Python and JavaScript docs overview pages, reference docs, state-graph reference docs, upstream GitHub, and the official product page, with candidate metadata recording `candidate_source=known_path_fallback`, the fallback reason, and the original provider. After verification, the gap analyzer inspects required answer-slot coverage; when required slots are missing or weak, it generates bounded supplemental search queries and the runner appends another search/fetch/parse/index/draft/verify round before reporting. Supplemental gap queries use deterministic per-slot variants so later rounds can try a different official/reference phrasing instead of stopping only because the first query already exists. If supplemental search only returns duplicate URLs, the gap round can still attempt existing unattempted high-value candidates before re-parsing, re-indexing, drafting, and verifying; if supplemental search itself is unavailable but existing source documents/chunks/claims can support a partial report, the runner records `gap_search_unavailable` / `supplemental_search_failed` warnings and continues to reporting instead of failing the task. `services/orchestrator/app/research_quality/` now centralizes shared answer-slot, source-intent, evidence-candidate, source-yield, evidence-yield, dropped-source reason, slot-coverage, and gap-analysis contracts so acquisition, pipeline diagnostics, answer-yield metrics, report manifests, and the frontend consume the same deterministic vocabulary. Claim drafting, source-yield diagnostics, verification, and report filtering share deterministic answer-role and evidence-lineage rules so navigation, project-meta, setup, diagram/config, generic documentation-pointer text, and weak lexical verifier matches do not enter the main answer as strongly supported facts.

For technical library or framework concept queries, source-intent classification is query-subject aware: generic tutorial pages with titles such as "What is X" are no longer treated as official/about sources unless the domain/path context is owned by or strongly tied to the queried project. LangGraph currently treats `docs.langchain.com`, `reference.langchain.com`, `langchain.com`, and `github.com/langchain-ai/langgraph` as owned high-value sources; localized mirrors such as `github.langchain.ac.cn`, `langgraph.com.cn`, and `langchain-doc.cn` remain secondary references. GitHub repository candidates are official only when the owner/repo matches the known upstream project, so third-party tutorial repositories do not get upstream README priority. The LangGraph product page remains a valid owned source, but it is ranked behind docs/reference/upstream GitHub for how-it-works overview acquisition so it does not crowd out implementation evidence. Job boards, freelance listings, SEO repost pages, and obvious listing URLs are treated as low quality for overview queries. The no-LLM planner and claim scorer now use generic framework mechanism terms such as state, graph, nodes, edges, workflow, orchestration, routing, durable execution, streaming, memory, checkpointing, human-in-the-loop, integrations, APIs, and limitations instead of SearXNG-specific metasearch terms for non-SearXNG subjects. Gap rounds ignore newly discovered low-value candidates before falling back to already discovered unattempted high-value candidates, and LangGraph gap searches add bounded owned-source queries for LangChain docs, reference docs, and the `langchain-ai/langgraph` repository.

The planned next LLM-assisted quality layer is documented in `plans/llm-assisted-source-judge-and-planner.md`. It preserves the current deterministic pipeline as the authority of record. The rollout order is planner-only, source judge shadow mode, source judge active reranking, gap reasoner, and grounded report writer hardening. The LLM planner may suggest bounded subquestions and search queries, but deterministic planner guardrails still preserve required searches and downrank unsafe suggestions. The LLM source judge will receive candidate URL metadata plus deterministic source-intent, low-value, and ownership signals; it may return a structured advisory label, confidence scores, rationale, and a bounded priority adjustment. It cannot mark a source official without deterministic ownership evidence, cannot override low-value/blocklist rules, and cannot promote mirrors or third-party GitHub tutorials above owned official docs/reference/upstream repositories. The LLM gap reasoner may explain missing slots and suggest query intents, but deterministic gap limits, dedupe, and fetch eligibility remain final. The grounded report writer remains the only report-prose LLM surface and must render only from verified claim/evidence/citation-span bundles.

## Layer boundaries

- UI / gateway layer: `apps/web/` now provides task creation, pre-run plan confirmation, queued worker start, polling task detail progress/events, source, claim, and Markdown report views
- orchestrator / workflow layer: `services/orchestrator/app/` now contains the thin research task API, request and response schemas, database dependencies, the task service layer, the host-local worker, the Phase 3 search-discovery seams, the Phase 4 acquisition service, the Phase 5 parsing service, the Phase 6 indexing service, the Phase 7 plus Phase 8 claims service, and the Phase 9 plus Phase 10 report-synthesis and deploy-hardening services
- persistence / ledger layer: `migrations/` and `packages/db/` hold the schema, ORM, session helpers, and repositories
- acquisition / parsing / indexing layer: the codebase now includes a minimal search-provider abstraction, a SearXNG-backed implementation, a policy-guarded HTTP acquisition client, a filesystem-backed and MinIO-backed snapshot storage seam, a minimal parser and chunker for `text/html` plus `text/plain`, MediaWiki/Wikipedia article-body extraction with paragraph fallback metadata, a chunk-index backend seam with a live-validatable OpenSearch REST implementation plus task-scoped retrieval, and deterministic claim-drafting plus verification helpers for support-only citation binding and minimal contradiction scanning; browser, Tika, embeddings, and reranking remain placeholders
- reporting / delivery layer: a minimal Markdown report synthesis path now exists inside orchestrator, while dedicated report service and export formats remain placeholders
- observability layer: `packages/observability/` now provides JSON-log configuration, request metrics, and key task/fetch/parse/verify/report counters

## Repository shape

- `services/orchestrator/`: runnable FastAPI service skeleton for future research task APIs
- `services/orchestrator/app/services/`: thin task state transition logic plus queueing, worker execution, Phase 3 search discovery, Phase 4 acquisition orchestration, Phase 5 parsing orchestration, Phase 6 indexing orchestration, Phase 7 plus Phase 8 claims orchestration, and Phase 9 report synthesis orchestration
- `services/orchestrator/app/search/`: provider abstraction, SearXNG client, query expansion, and URL canonicalization helpers
- `services/orchestrator/app/llm/`: optional noop and OpenAI-compatible provider seam for planner-only use
- `services/orchestrator/app/planning/`: Research Planner v1 dataclasses, JSON parsing, deterministic fallback, and planner service
- `services/orchestrator/app/acquisition/`: HTTP acquisition policy and fetch client
- `services/orchestrator/app/parsing/`: minimal HTML and plain-text extraction plus stable chunking helpers
- `services/orchestrator/app/storage/`: snapshot and artifact object-store interface plus filesystem and MinIO backends
- `services/orchestrator/app/indexing/`: chunk-index backend abstraction plus the minimal OpenSearch REST implementation with Phase 10 validation and error wrapping
- `services/orchestrator/app/research_quality/`: shared deterministic source-intent classification, answer-slot coverage, evidence-candidate, source-yield, evidence-yield, dropped-source reason, and slot-coverage contracts used across selection, diagnostics, verification, and reporting
- `services/orchestrator/app/claims/`: deterministic Phase 7 and Phase 8 helpers for claim sentence selection, confidence heuristics, citation span validation, and minimal verification conflict handling
- `services/orchestrator/app/reporting/`: deterministic Phase 9 Markdown report rendering, optional grounded LLM report writing, report-language helpers, and Phase 10 manifest helpers
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
- keep parsing and chunking explicit: only `text/html` and `text/plain`, minimal body extraction, MediaWiki paragraph fallback when strict article extraction would be empty, and a stable paragraph-window chunker
- keep indexing and retrieval explicit: deterministic `source_chunk_id` traceability, task-scoped filtering, simple match retrieval, and thin debug APIs only
- keep claim drafting explicit: support-only evidence binding, draft-only verification status, deterministic query-aware sentence scoring/selection, conservative explanatory fallback only after strict filters produce no claims, no-claims diagnostics in pipeline failure details, and exact offset plus excerpt validation against `source_chunk.text`
- keep deterministic claim quality filters conservative: skip short fragments, title/question-like statements, figure captions, diagram/config fragments, incomplete sentences, and case/punctuation duplicates before claim persistence or report rendering
- keep optional planner output bounded by deterministic guardrails: for definition or overview queries, stable reference domains such as Wikipedia are not treated as hard avoids, original/user query plus official/about/Wikipedia/GitHub README guardrail searches are retained, SearXNG overview runs add deterministic known-path candidates for `docs.searxng.org/user/about.html` and `en.wikipedia.org/wiki/SearXNG` when official SearXNG results are present, and official about/reference sources outrank admin architecture, installation, API, or developer pages unless the user asks for those topics
- keep planned LLM-assisted source judgment advisory-first: shadow mode must not change ranking; active mode must be separately flagged, bounded, fully audited, and unable to override ownership evidence, blocklists, low-value source filters, SSRF/acquisition policy, or official-source deterministic priority
- keep answer-yield recovery deterministic: if claim drafting creates no claims, or creates only insufficient coverage for a what/how query, one bounded supplemental acquisition pass may fetch 1-3 unattempted high-value sources before parsing, indexing, and drafting are retried once
- keep gap recovery deterministic: after verification, required answer slots that are missing or weak may trigger up to `RESEARCH_GAP_MAX_ROUNDS` supplemental search/fetch/parse/index/draft/verify rounds before reporting
- keep technical concept source selection deterministic: title-only generic tutorials must not be promoted to official/about, official docs/reference/GitHub candidates should be attempted before generic articles when the query subject matches owned project metadata, localized mirrors should remain secondary unless explicitly whitelisted as owned, third-party GitHub tutorials should not receive upstream repository priority, and job/freelance/listing pages should stay low quality for overview queries
- keep answer slots explicit and deterministic: planning and reporting may expose query-specific slots, but slot coverage must be derived from persisted claim categories and evidence-backed claims rather than LLM-written facts
- keep verification explicit: support and contradict evidence only, deterministic span selection, and the minimum stable verification statuses `draft`, `supported`, `mixed`, and `unsupported`
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
