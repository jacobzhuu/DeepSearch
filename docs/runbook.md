# Runbook

## Current project positioning

- this is now a single-operator, self-hosted research platform
- the recommended runtime path is host-local Linux
- the current completed functional loop is:
  - `task -> search -> fetch -> parse -> index -> draft -> verify -> report`
- Docker and compose may stay in the repository as optional tooling, but they are not the primary route or acceptance standard
- product changes in this closeout are limited to conflict fixes and the narrow pre-run planner workflow needed to keep web testing honest about smoke versus real research mode

## Recommended runtime path

Run the system directly on a Linux host with:

- Python environment for the orchestrator
- PostgreSQL
- OpenSearch
- one object-store mode:
  - filesystem backend for simplest local operation
  - MinIO when S3-like object storage is preferred
- SearXNG-compatible endpoint or the repository-local search mock for smoke validation

Optional tooling still present in the repo:

- `docker-compose.yml`
- `docker-compose.dev.yml`
- `.env.compose.example`

They are convenience packaging only. They are not the current primary operator path.

## What is implemented now

The current v1 path supports:

- task creation, mutation, event stream, and revision tracking
- task list API and web task list page for recent `research_task` records
- worker-executed search discovery with canonicalized candidate URLs
- worker-executed HTTP acquisition with fetch jobs, attempts, and stored snapshots
- worker-executed parsing for `text/html`, `text/plain`, safe raw text formats, and the documented
  multiformat parser inputs
- task-scoped chunk indexing and retrieval through OpenSearch
- candidate claim drafting with citation span binding, query-aware deterministic claim scoring, and answer-focused top-K selection
- deterministic claim verification with `support`, `weak_support`, and `contradict` evidence and `supported` / `unsupported` / `mixed` / `contradicted` statuses
- Markdown report synthesis backed by persisted report artifacts, with low-quality/off-query claim filtering and low answer-coverage warnings
- report language selection from task constraints; the web workspace sends `zh-CN` by default
- optional grounded LLM report writing that receives only verified claim/evidence/citation-span bundles and falls back to deterministic Markdown on invalid output or provider failure
- PDF, DOCX, PPTX, and XLSX text extraction through the same source/chunk/index/evidence/report path as HTML/plain text, with parser metadata and locator fallbacks exposed in APIs
- deterministic retrieval/rerank diagnostics in retrieved chunk metadata
- optional shadow LLM source judging; disabled by default and never used for final ranking in this MVP
- shared deterministic source-intent, answer-slot, evidence-candidate, source-yield, evidence-yield, dropped-source reason, slot-coverage, and gap-analysis contracts for source selection, diagnostics, verification, and report coverage
- deterministic source/chunk quality scoring for prioritization and diagnostics, including authority, relevance, crawlability, information density, safety, and explicit unknown freshness
- task-event and task-detail observability for planner guardrails, final search queries, search-query diagnostics, known-path fallback injection, source selection, answer slots, source yield, evidence yield, slot coverage, gap rounds, answer yield, answer coverage, verifier strong/weak support counts, supplemental acquisition, fetch success/failure counts, failed fetch reasons, parse decisions, and actionable failure diagnostics
- pre-run research planning from the web workspace: create a `research_task`, generate a bounded plan, optionally edit its JSON, then confirm and queue the worker pipeline
- status-aware web controls for Run, Pause, Resume, and Cancel; pause/cancel are observed by the worker at pipeline stage boundaries
- visible runtime-mode warnings when `SEARCH_PROVIDER=smoke`, `INDEX_BACKEND=local`, or no LLM planner is active
- report page HTML rendering plus Raw Markdown, Copy Markdown, and Download `.md` controls
- JSON logs and basic metrics

## What is intentionally not being expanded now

- OpenClaw
- HTML export
- PDF export
- LLM source judge, active LLM reranking, and LLM gap reasoner implementation
- complex verifier logic
- complex retrieval optimization
- distributed worker leases or external queue infrastructure

## Environment variables

### Core app

| Variable | Purpose | Default |
| --- | --- | --- |
| `APP_NAME` | FastAPI title and service label | `deepresearch-orchestrator` |
| `APP_ENV` | Operator environment label | `development` |
| `APP_HOST` | Bind host | `0.0.0.0` |
| `APP_PORT` | Bind port | `8000` |
| `LOG_LEVEL` | Root logger level | `INFO` |
| `LOG_FORMAT` | Log format; current supported value is `json` | `json` |
| `METRICS_ENABLED` | Enable `GET /metrics` | `true` |

### Database

| Variable | Purpose | Default |
| --- | --- | --- |
| `DATABASE_URL` | SQLAlchemy and Alembic database URL | `sqlite:///./data/dev.db` in `.env.example` |

### Search and acquisition

| Variable | Purpose | Default |
| --- | --- | --- |
| `SEARCH_PROVIDER` | `searxng` for real search or `smoke` for explicit development smoke mode | `searxng` |
| `SEARXNG_BASE_URL` | SearXNG-compatible search endpoint | `http://127.0.0.1:8080` |
| `SEARXNG_TIMEOUT_SECONDS` | Search timeout | `10` |
| `SEARCH_MAX_RESULTS_PER_QUERY` | Max raw results per expanded query | `10` |
| `QUERY_EXPANSION_MAX_DOMAINS` | Max `site:` expansions | `3` |
| `ACQUISITION_TIMEOUT_SECONDS` | HTTP fetch timeout | `10` |
| `ACQUISITION_MAX_REDIRECTS` | Redirect cap | `3` |
| `ACQUISITION_MAX_RESPONSE_BYTES` | Response byte cap | `1048576` |
| `ACQUISITION_MAX_CANDIDATES_PER_REQUEST` | Max candidates per `POST /fetches` | `5` |
| `ACQUISITION_TARGET_SUCCESSFUL_SNAPSHOTS` | Target successful snapshots before ordinary acquisition may stop | `2` |
| `ACQUISITION_MIN_ANSWER_SOURCES` | Minimum answer-source target for planner-enabled overview runs | `3` |
| `ACQUISITION_MAX_SUPPLEMENTAL_SOURCES` | Max unattempted high-value sources in one supplemental pass | `3` |
| `RESEARCH_GAP_MAX_ROUNDS` | Max supplemental gap-analysis rounds after verification | `2` |
| `RESEARCH_GAP_MAX_QUERIES_PER_ROUND` | Max deterministic supplemental queries per gap round | `4` |
| `RESEARCH_WORKER_POLL_INTERVAL_SECONDS` | Host-local worker idle poll interval | `2` |
| `RESEARCH_WORKER_BATCH_SIZE` | Queued tasks processed per worker poll | `1` |
| `ACQUISITION_USER_AGENT` | Acquisition user agent | `deepresearch-orchestrator/0.1` |

Parser support in this MVP:

- supported: `text/html`, `text/plain`, safe raw text formats such as Markdown/YAML/env,
  `application/pdf`, DOCX, PPTX, XLSX OpenXML MIME types
- unsupported MIME types are skipped with an auditable parse decision
- Office macros, scripts, external resources, and embedded objects are not executed
- PDF page numbers are best-effort; unreliable cases record a locator fallback reason
- browser-rendered fetch and recursive attachment crawling are deferred until a stronger sandbox and
  parent/child source model are added

### Object storage

| Variable | Purpose | Default |
| --- | --- | --- |
| `SNAPSHOT_STORAGE_BACKEND` | `filesystem` or `minio` | `filesystem` |
| `SNAPSHOT_STORAGE_ROOT` | Filesystem object root | `./data/snapshots` |
| `MINIO_ENDPOINT` | MinIO endpoint when using `minio` backend | empty |
| `MINIO_ACCESS_KEY` | MinIO access key | empty |
| `MINIO_SECRET_KEY` | MinIO secret key | empty |
| `MINIO_SECURE` | MinIO TLS toggle | `false` |
| `MINIO_REGION` | Optional region | empty |
| `SNAPSHOT_STORAGE_BUCKET` | Snapshot bucket | `snapshots` |
| `REPORT_STORAGE_BUCKET` | Report bucket | `reports` |

### OpenSearch

| Variable | Purpose | Default |
| --- | --- | --- |
| `INDEX_BACKEND` | Index backend seam | `opensearch` |
| `OPENSEARCH_BASE_URL` | OpenSearch endpoint | `http://127.0.0.1:9200` |
| `OPENSEARCH_INDEX_NAME` | Chunk index name | `source-chunks-v1` |
| `OPENSEARCH_USERNAME` | Basic-auth username | empty |
| `OPENSEARCH_PASSWORD` | Basic-auth password | empty |
| `OPENSEARCH_VERIFY_TLS` | TLS verification toggle | `true` |
| `OPENSEARCH_CA_BUNDLE_PATH` | Optional CA bundle path | empty |
| `OPENSEARCH_TIMEOUT_SECONDS` | Request timeout | `10` |
| `OPENSEARCH_VALIDATE_CONNECTIVITY_ON_STARTUP` | Live connectivity probe on startup | `false` |
| `INDEXING_MAX_CHUNKS_PER_REQUEST` | Max chunks per `POST /index` | `20` |
| `RETRIEVAL_MAX_RESULTS_PER_REQUEST` | Max retrieval results per request | `20` |

Development-only note:

- `SEARCH_PROVIDER=smoke` returns clearly marked synthetic `deepsearch-smoke.local` fixture sources and uses a network-free smoke acquisition client; it is not real search
- `INDEX_BACKEND=local` uses an in-process deterministic index; it is not durable and is not a replacement for OpenSearch
- together these report `running_mode=smoke-search+deterministic-local+no-LLM`
- the web UI now surfaces this as a connectivity-test mode before research starts; do not evaluate product-quality report output from smoke fixtures

### Claims and report

| Variable | Purpose | Default |
| --- | --- | --- |
| `CLAIM_DRAFTING_MAX_CANDIDATES_PER_REQUEST` | Max retrieval candidates for drafting | `5` |
| `CLAIM_VERIFICATION_MAX_CLAIMS_PER_REQUEST` | Max claims per verification request | `5` |

Claim drafting is deterministic and no-LLM. For definition/mechanism queries such as `What is SearXNG and how does it work?`, the selector now assigns an `answer_role` and prefers definition, mechanism, privacy/design-goal, feature, and low-priority deployment/self-hosting sentences. It rejects contribution calls-to-action, community logistics, documentation pointers, promotional slogans, lowercase fragments, setup/getting-started instructions, diagram/config fragments, and broken-link residue such as `listed at .` before claim persistence. Scoring metadata is stored in `claim.notes_json` and regenerated reports use that metadata to exclude low-quality, setup, unsupported-category, or off-query supported claims from the report body.

Deployment queries such as `How to deploy SearXNG with Docker?` use a separate evidence path for commands and configuration. SearXNG Docker tasks can inject the official installation docs, `github.com/searxng/searxng-docker`, raw GitHub README candidates for repository pages, and raw compose/env example candidates; the `searxng/searxng-docker` repository is classified as `official_repository`, and archived/superseded repository status can be reported as a limitation/maintenance caveat. Docker commands, Compose YAML, ports, volumes, prerequisites, `settings.yml`, `SEARXNG_SECRET` / other `SEARXNG_*` values, reverse proxy / limiter / secret / custom-certificate guidance, troubleshooting text, and update/maintenance commands may be drafted as `deployment_code_or_config` evidence with deployment `slot_ids`, then verified against exact citation spans before any report renders them. Deployment drafting uses a deployment-specific cap above the generic claim limit and applies both slot-diverse and marker-diverse selection so exact snippets such as `sudo usermod -aG docker`, `docker compose pull`, `.env`, `SEARXNG_*`, reverse proxy, limiter/bot protection, certificates, and troubleshooting commands survive when they are already present in parsed chunks. Security slot coverage is intentionally strict: reverse proxy, limiter/bot protection, secrets, certificates, and public instance exposure count; `docker exec ... root` is troubleshooting only, and `FORCE_OWNERSHIP` is volume/configuration evidence only. Command/config evidence is rendered as fenced Markdown code blocks with claim/evidence/citation traceability, preferring the complete claim statement or persisted full evidence excerpt over a shortened citation excerpt. If a required deployment slot has no verified command/config evidence, the report should show a coverage gap rather than a generic deployment answer.

Evidence-quality metadata is a code-level contract, not a new table. Source/chunk APIs expose source quality fields from existing `source_document` score columns plus chunk metadata. Claim notes and task/report diagnostics may include `evidence_candidate_id`, `slot_ids`, `source_intent`, `evidence_kind`, citation span ids, claim evidence ids, source-yield rows, evidence-yield summaries, verification evidence rank scores, citation precision, chunk/span/content reuse diagnostics, and slot-coverage summaries. Dropped-source reasons use this taxonomy: `not_selected_low_priority`, `blocked_by_policy`, `fetch_failed`, `unsupported_content_type`, `parse_failed`, `low_chunk_quality`, `no_evidence_candidates`, `evidence_rejected`, `duplicate_or_near_duplicate`, `off_intent`, and `unknown`.

Backward compatibility note: old tasks and report artifacts may not have the newer diagnostics payloads. The API and benchmark script normalize missing `source_yield_summary`, `dropped_sources`, and `slot_coverage_summary` to `[]`, and missing `evidence_yield_summary` or `verification_summary` to `{}` whenever an observability payload exists. Older claim notes without `evidence_candidate_id` remain reportable through their persisted citation spans.

Verification is deterministic lexical verification, not full entailment. Drafting persists `candidate_support` evidence; verification persists only selected `support`, `weak_support`, and `contradict` relations after ranking by lexical match, source quality, chunk quality, information density, retrieval score, and source/content diversity. The verifier now prefers sentence spans and short adjacent-sentence spans over coarse chunk fallbacks, and applies a small batch-local reuse penalty when a chunk/span/content identity has already been used by earlier claims. Strong support without contradiction becomes `supported`; strong support plus contradiction becomes `mixed`; contradiction without strong support becomes `contradicted`; weak-only evidence remains `unsupported`. The verifier records strong support, weak support, contradiction, shallow-overlap, numeric/date mismatch, scope-mismatch, citation precision, reuse penalties/counts, and dropped/selected evidence counts in `claim.notes_json["verification"]`. Reports keep weak lexical support out of the main answer sections.

If strict claim filters produce no claims, the service runs a narrow deterministic fallback over explanatory definition, mechanism, privacy, or feature sentences only. It does not promote short slogans such as `Search without being tracked.`, contribution/community text, navigation, references, redirect stubs, or setup-only instructions unless the query explicitly asks for that material. Fallback claims are marked in `claim.notes_json` with `draft_mode = "fallback_relaxed"`, `fallback_reason`, and `original_rejected_reason`.

The pipeline also computes answer-yield metrics per `source_document`, separating raw `candidate_sentence_count` from `answer_relevant_candidate_count` and final accepted claim counts. If claim drafting creates zero claims, or a what/how query produces only one claim without required definition/mechanism coverage, it runs at most one supplemental acquisition pass over unattempted high-value candidates such as official about pages, Wikipedia references, official home pages, upstream GitHub README/repo pages, or generic articles. Developer, API, architecture, installation, social, forum, and video pages stay downranked unless the query explicitly asks for them.

For technical library/framework overview queries such as `What is LangGraph and how does it work?`, deterministic source selection now requires owned project-domain or upstream repository evidence before classifying a result as `official_about`, `official_docs_reference`, or `github_readme_or_repo`. For LangGraph, `docs.langchain.com`, `reference.langchain.com`, `langchain.com`, and `github.com/langchain-ai/langgraph` are high-value owned sources; `github.langchain.ac.cn`, `langgraph.com.cn`, `langchain-doc.cn`, and third-party GitHub tutorial repositories are secondary references, not official-owned sources. If main `SEARCHING` gets `searxng_empty_results_with_unresponsive_engines` for LangGraph before any candidate URLs exist, the runner injects bounded known-path candidates for Python docs, JavaScript docs, reference docs, state-graph reference docs, upstream GitHub, and the official product page, then continues to acquisition. Injected candidates are marked with `candidate_source=known_path_fallback`, `fallback_reason`, and `original_search_provider`. The product page remains available but is ranked behind docs/reference/upstream GitHub for how-it-works acquisition. Job boards, freelance sites, job-search URLs, SEO repost pages, and obvious unrelated listings are low quality for overview queries. If gap rounds still find missing required slots and search returns duplicates or low-value results, the runner falls back to existing unattempted high-value candidates and LangGraph gap analysis emits bounded targeted searches for LangChain docs/reference/GitHub. No-LLM planning and claim scoring use framework-oriented mechanism and feature terms such as graph/state/nodes/edges/workflow/orchestration/routing, durable execution, streaming, memory, checkpointing, human-in-the-loop, integrations, APIs, and limitations.

### Optional LLM planner

| Variable | Purpose | Default |
| --- | --- | --- |
| `LLM_ENABLED` | Master switch for LLM provider construction | `false` |
| `LLM_PROVIDER` | `noop` or OpenAI-compatible aliases: `openai-compatible`, `openai_compatible`, `openai` | `noop` |
| `LLM_MODEL` | Provider model name | empty |
| `LLM_API_KEY` | Provider API key from env or `.env`; never log or expose this value | empty |
| `LLM_BASE_URL` | OpenAI-compatible API base URL, usually ending in `/v1` | empty |
| `LLM_TIMEOUT_SECONDS` | LLM HTTP timeout | `30` |
| `LLM_MAX_RETRIES` | Retry count for retryable LLM provider errors | `1` |
| `LLM_MAX_OUTPUT_TOKENS` | Planner response token cap | `1200` |
| `LLM_REPORT_WRITER_ENABLED` | Enable grounded LLM report writer for final Markdown synthesis | `false` |
| `LLM_REPORT_MAX_OUTPUT_TOKENS` | Grounded report-writer response token cap | `2400` |
| `LLM_SOURCE_JUDGE_ENABLED` | Enable shadow source-judge diagnostics | `false` |
| `LLM_SOURCE_JUDGE_ACTIVE_RERANK` | Reserved active rerank flag; remains disabled in this MVP | `false` |
| `LLM_SOURCE_JUDGE_MAX_CANDIDATES` | Max source candidates judged per search stage | `5` |
| `RESEARCH_PLANNER_ENABLED` | Run planner before search when `LLM_ENABLED=true` | `false` |
| `RESEARCH_PLANNER_MAX_SUBQUESTIONS` | Planner subquestion cap | `5` |
| `RESEARCH_PLANNER_MAX_SEARCH_QUERIES` | Planner search-query cap | `8` |

The planner is disabled by default. The web workspace can still create a deterministic fallback plan through `POST /api/v1/research/tasks/{task_id}/plan`; this is useful for reviewing search intent but is not an LLM-generated plan. When LLM planning is enabled, the same endpoint uses the configured provider and requires strict structured JSON with the top-level keys listed in the planner prompt. Valid output must be one JSON object, or one safely extractable fenced JSON object; unfenced prose around JSON is rejected. Planner search queries may use only these `expected_source_type` values: `general_web`, `official_docs`, `official_about`, `official_installation_admin`, `official_or_reference`, `official_repository`, `github_readme_or_repo`, and `reference`. Missing keys, invalid JSON, schema failures, provider errors, timeouts, and unavailable configuration do not fail the task; they produce a deterministic fallback plan with `planner_status=fallback`, `plan_source=deterministic_fallback_after_llm_failure`, warning text `LLM planner failed validation/provider call; deterministic fallback was used.`, and planner diagnostics in the persisted `research_plan.created` event. Successful LLM plans use `planner_status=success`, `plan_source=llm_planner`, and `LLM planner generated this research plan.` Disabled planner runs use `No LLM planner is active; deterministic planner used.` Pipeline execution also emits `research_plan.failed` for the provider/planner failure, then records the deterministic fallback as `research_plan.created` and continues. Both generated and operator-edited plans emit `research_plan.created` before `SEARCHING`, and the pipeline reuses the latest current-revision plan instead of generating a hidden duplicate.

Planner diagnostics are sanitized for task events and task detail. They include parse-stage flags for `raw_text`, `json_extracted`, and `schema_validated`, a capped raw-output preview, a raw-output hash, the JSON extraction method or extraction error, and validation errors categorized as missing field, extra field, invalid enum value, wrong type, or generic validation error. If an operator edits a fallback plan, task detail preserves the prior diagnostics so the original provider or schema failure remains actionable.

For definition and overview queries such as `What is SearXNG and how does it work?`,
planner output is post-processed as a bounded suggestion. Wikipedia cannot be treated as a
hard avoid domain for that query type. Final query planning always preserves the original
query plus official documentation, about/how-it-works, Wikipedia, and software-project
GitHub README searches when an entity can be extracted. SearXNG overview runs also add
deterministic known-path candidates for `https://docs.searxng.org/user/about.html` and
`https://en.wikipedia.org/wiki/SearXNG` when official SearXNG search results are present.
For `What is LangGraph and how does it work?`, LLM plans are merged with deterministic
owned-source guardrails for `docs.langchain.com`, `reference.langchain.com`,
`www.langchain.com/langgraph`, and `github.com/langchain-ai/langgraph`; broad
`github.com/langchain-ai` preferences are supplemented with the concrete LangGraph repo,
and `langchain-ai.github.io` / `blog.langchain.dev` are marked secondary/downweighted
unless independently verified. The final planner diagnostics expose these query and domain
corrections in `final_search_queries`, `dropped_or_downweighted_planner_queries`,
`source_preferences.secondary_preferred_domains`, and `source_preferences.planner_domain_corrections`.
Source selection prioritizes official about/reference pages over admin architecture,
installation, API, or developer pages unless the user explicitly asks about those topics.

The planner never writes claims. Claim drafting, evidence binding, and verification remain deterministic and source-backed. Markdown report synthesis is deterministic by default; when `LLM_REPORT_WRITER_ENABLED=true`, the grounded report writer may write report prose but receives only verified claims, claim-evidence ids, and citation-span excerpts. Rendered LLM items are dropped unless their claim/evidence/citation ids validate against the prepared report bundle, and failures fall back to deterministic Markdown.

Planned LLM-assisted source-quality work is tracked in `plans/llm-assisted-source-judge-and-planner.md`. The rollout order is:

1. LLM planner only, with deterministic guardrails final.
2. LLM source judge in shadow mode, adding diagnostics but no ranking changes.
3. LLM source judge active reranking behind a separate flag, with bounded score adjustments.
4. LLM gap reasoner, with deterministic max rounds, dedupe, and fetch limits final.
5. Grounded report writer hardening, with deterministic Markdown fallback.

The source judge is now available in shadow mode through `LLM_SOURCE_JUDGE_ENABLED=true`. It receives only bounded candidate URL metadata, search snippets, deterministic source-intent results, low-value signals, and ownership evidence. It returns strict structured JSON with an allowed label, confidence, reasons, and a bounded priority adjustment. It cannot mark a source official unless deterministic ownership evidence already supports that conclusion. It cannot override job/freelance/listing filters, blocklists, SSRF/acquisition policy, MIME policy, or official-owned source priority. In this MVP it records additive diagnostics only and always sets `used_in_final_ranking=false`.

Noop planner validation:

```bash
LLM_ENABLED=true \
LLM_PROVIDER=noop \
RESEARCH_PLANNER_ENABLED=true \
python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000
```

DeepSeek planner configuration uses the OpenAI-compatible provider. Both
`https://api.deepseek.com` and `https://api.deepseek.com/v1` are accepted base URLs; the
provider appends `/chat/completions` without duplicating path segments.

OpenAI-compatible planner configuration uses placeholders only:

```bash
LLM_ENABLED=true
LLM_PROVIDER=openai_compatible
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
RESEARCH_PLANNER_ENABLED=true
LLM_REPORT_WRITER_ENABLED=false
```

Planner-only mode leaves claim drafting, verification, gap analysis, and report writing deterministic. Use `progress.observability.planner_status`, `progress.observability.plan_source`, `progress.observability.research_plan.planner_diagnostics`, and `research_plan.created` / `research_plan.failed` events to confirm whether a task used an LLM plan or deterministic fallback. For successful DeepSeek planner acceptance, expect `planner_status=success`, `plan_source=llm_planner`, and `research_plan.planner_diagnostics.schema_validated=true`. If the planner still falls back, inspect `planner_diagnostics.validation_errors`, `planner_diagnostics.raw_output_preview`, and `planner_diagnostics.json_extraction_error` to identify the exact failure. Do not enable source judge, gap reasoner, or grounded report-writer flags for Phase 1 validation.

Grounded report-writer configuration uses the same provider, but is controlled independently:

```bash
LLM_ENABLED=true
LLM_PROVIDER=openai_compatible
LLM_API_KEY=sk-...
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-chat
LLM_REPORT_WRITER_ENABLED=true
LLM_REPORT_MAX_OUTPUT_TOKENS=2400
```

To generate Chinese reports from API tooling, send either top-level `report_language` or a
task constraint:

```json
{
  "query": "How to deploy SearXNG with Docker?",
  "report_language": "zh-CN",
  "constraints": {
    "language": "zh-CN"
  }
}
```

On task creation, top-level `report_language` also fills `constraints.language` when the
request did not already provide one. On revision, top-level `report_language` changes only
the report language; send `constraints.language` explicitly if the search/planning language
should change too. The grounded report writer receives the resolved language in prompt
metadata and the grounding bundle. If `report_language=zh-CN` and the LLM returns an
English-only structured payload, the writer is treated as a validation failure and the task
falls back to deterministic Chinese Markdown.

DeepSeek planner smoke test:

```bash
source /root/anaconda3/etc/profile.d/conda.sh
conda activate deepsearch311
cd /share/zhuzy/projects/DeepSearch
python scripts/smoke_deepseek_planner.py
```

The script reads `.env`, calls only the planner/provider layer, and does not create a task or
run the full pipeline.

Live SearXNG Docker deployment acceptance:

```bash
source /root/anaconda3/etc/profile.d/conda.sh
conda activate deepsearch311
cd /share/zhuzy/projects/DeepSearch
DEV_ENV_FILE=.env.deepseek.local DEV_SKIP_FRONTEND=true DEV_BACKEND_RELOAD=false ./dev.sh restart
python scripts/live_deployment_acceptance.py \
  --base-url http://127.0.0.1:8000 \
  --artifact-dir /tmp/deepsearch-live-deployment-acceptance
```

This script creates a fresh `How to deploy SearXNG with Docker?` task with
`report_language=zh-CN`, queues the real worker pipeline, waits for a terminal state, exports
`source_chunks`, `claims`, `claim_evidence`, and report Markdown, then validates the deployment
evidence terms across those layers. It exits `0` only when the fresh run completes through
`real-search+opensearch+planner+report-LLM`, produces a grounded Chinese report, preserves
expected Docker deployment evidence through downstream claim/evidence/report layers, and includes
claim / claim_evidence / citation traceability.

Phase benchmark commands:

```bash
python3 scripts/phase2_multiformat_benchmark.py \
  --base-url http://127.0.0.1:8000 \
  --wait-seconds 420 \
  --json-output /tmp/deepsearch-phase2-benchmark.json \
  --markdown-output /tmp/deepsearch-phase2-benchmark.md

python3 scripts/phase3_intelligence_benchmark.py \
  --base-url http://127.0.0.1:8000 \
  --wait-seconds 420 \
  --json-output /tmp/deepsearch-phase3-benchmark.json \
  --markdown-output /tmp/deepsearch-phase3-benchmark.md
```
warnings. It never prints `LLM_API_KEY`. Missing `LLM_API_KEY` exits with code `2`; sanitized
provider/planner failures exit with code `1`.

DeepSeek planner end-to-end manual validation:

1. Set `.env` values:

```bash
LLM_ENABLED=true
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://api.deepseek.com
LLM_API_KEY=<your real key; do not commit this>
LLM_MODEL=deepseek-chat
RESEARCH_PLANNER_ENABLED=true
```

2. Restart the orchestrator:

```bash
cd /share/zhuzy/projects/DeepSearch
pkill -f "services.orchestrator.app.main:app" || true

nohup bash -lc '
source /root/anaconda3/etc/profile.d/conda.sh
conda activate deepsearch311

export HTTP_PROXY="http://127.0.0.1:7890"
export HTTPS_PROXY="http://127.0.0.1:7890"
export ALL_PROXY="socks5://127.0.0.1:7890"
export NO_PROXY="127.0.0.1,localhost"

cd /share/zhuzy/projects/DeepSearch
PYTHONPATH=. uvicorn services.orchestrator.app.main:app --host 0.0.0.0 --port 8000
' > /share/zhuzy/projects/DeepSearch/orchestrator.log 2>&1 &
```

3. Start the host-local worker in a second shell:

```bash
cd /share/zhuzy/projects/DeepSearch
PYTHONPATH=. python scripts/research_worker.py
```

4. Create a task and queue it:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/research/tasks \
  -H "Content-Type: application/json" \
  -d '{"query":"What is SearXNG and how does it work?","constraints":{}}' \
  | python3 -m json.tool

TASK_ID=<task_id from the create response>

curl -s -X POST http://127.0.0.1:8000/api/v1/research/tasks/$TASK_ID/run \
  | python3 -m json.tool
```

The run response returns `status = "QUEUED"`. The worker advances the task through runtime stages.

5. Poll events:

```bash
curl -s http://127.0.0.1:8000/api/v1/research/tasks/$TASK_ID/events \
  | python3 -m json.tool \
  | grep -E "research_plan|planner|subquestions|search_queries" -n
```

6. Check the report after the task reaches `COMPLETED`:

```bash
curl -s http://127.0.0.1:8000/api/v1/research/tasks/$TASK_ID/report \
  | python3 -m json.tool
```

Full planner-pipeline smoke helper:

```bash
cd /share/zhuzy/projects/DeepSearch

python scripts/smoke_planner_pipeline.py \
  --query "What is SearXNG and how does it work?" \
  --base-url http://127.0.0.1:8000
```

The helper reads `.env`, creates a task, queues `POST /api/v1/research/tasks/<task_id>/run`,
and polls task status/events until the worker completes or fails. It prints task id, status,
running mode, planner status, final search queries, attempted sources, source documents,
chunk count, claims by category, report artifact id/version, report preview, and failure
details. It never prints API keys. Exit code `0` means the worker-completed pipeline produced
at least one `source_document`, one `source_chunk`, one claim, and a readable Markdown
`report_artifact`; `1` means the pipeline failed or produced an insufficient ledger; `2`
means the service, worker, or configuration is unavailable.

Generalization benchmark query list:

```bash
python scripts/benchmark_queries.py --json
```

Run the benchmark against a live orchestrator and backing services:

```bash
python scripts/benchmark_queries.py \
  --run \
  --base-url http://127.0.0.1:8000 \
  --json
```

The benchmark uses ten representative queries covering SearXNG, OpenSearch, LangGraph, MCP,
Dify, privacy limitations, Docker deployment, vendor comparison, RAG limitations, and current
Deep Research product comparison. It is a regression harness for source-intent, answer-slot,
source-yield, evidence-yield, verifier, gap-round recovery, and report-structure generalization,
not a golden-output text comparison. With `--run --json`, each task is queued through `/run`,
then polled until it leaves `QUEUED`/runtime statuses or `--wait-seconds` expires. Each row
includes slot coverage, source yield, evidence yield, verification summary, and a non-SearXNG
contamination check.

Useful benchmark narrowing options:

```bash
python scripts/benchmark_queries.py --json --limit 2
python scripts/benchmark_queries.py --json --query-id 3
python scripts/benchmark_queries.py --run --limit 2 --wait-seconds 420 --output /tmp/deepsearch-benchmark.json
```

Evidence quality benchmark:

```bash
python scripts/evidence_quality_benchmark.py \
  --base-url http://127.0.0.1:8000 \
  --wait-seconds 420 \
  --json-output /tmp/deepsearch-evidence-benchmark.json \
  --markdown-output /tmp/deepsearch-evidence-benchmark.md
```

This benchmark runs 3-5 real research questions through `/run` and reads only persisted API
outputs. It reports source count, chunk count, claim count, supported/unsupported/mixed/
contradicted distribution, average source quality, verified evidence per claim, verified
citation-span precision, duplicate source rate, verified evidence content duplicate rate,
all-evidence duplicate rate, chunk/span reuse counts, top reused chunks/spans, per-query duplicate
content rate, per-claim evidence diversity diagnostics, and whether a report artifact exists.
Draft `candidate_support` rows remain visible in the all-evidence diagnostics, but precision and
primary duplicate metrics are computed on verifier-created `support` / `weak_support` /
`contradict` evidence. It does not hardcode expected answers and should be treated as a
quality-exposure harness rather than a pass/fail golden-answer test.

Before trusting the default `http://127.0.0.1:8000` service, check that it is the current
working-tree process:

```bash
curl -s http://127.0.0.1:8000/versionz | python3 -m json.tool
curl -s http://127.0.0.1:8000/openapi.json \
  | rg 'source_yield_summary|evidence_yield_summary|slot_coverage_summary|verification_summary'
```

To restart the host-local backend and frontend from the current checkout, prefer the
managed helper:

```bash
cd /share/zhuzy/projects/DeepSearch
./dev.sh restart
./dev.sh status
```

The helper is intentionally host-local first:

- it binds backend and frontend to `127.0.0.1` unless `DEV_BACKEND_HOST` or
  `DEV_FRONTEND_HOST` is set explicitly
- it loads `.env` without shell evaluation and lets already-exported environment
  variables override `.env` values
- it stops only PID files that match the expected backend, frontend, or mock-search
  commands
- it starts processes in dedicated process groups so repeated `restart` and `stop`
  calls converge cleanly
- it writes diagnostics to `.logs/` and process metadata to `.run/`

Common commands:

```bash
./dev.sh doctor
./dev.sh logs backend
./dev.sh logs frontend
./dev.sh smoke http://127.0.0.1:8000
./dev.sh stop
```

When `SEARCH_PROVIDER=smoke`, `./dev.sh smoke` automatically uses
`deepsearch-smoke.local` as the allowed smoke domain and `smoke` as the claim query unless
those options are passed explicitly.

For a deterministic development run without real search or OpenSearch:

```bash
cd /share/zhuzy/projects/DeepSearch
APP_ENV=development \
SEARCH_PROVIDER=smoke \
INDEX_BACKEND=local \
SNAPSHOT_STORAGE_BACKEND=filesystem \
./dev.sh restart
```

For direct LAN access, opt in explicitly and restrict ports with the host firewall:

```bash
DEV_BACKEND_HOST=0.0.0.0 DEV_FRONTEND_HOST=0.0.0.0 ./dev.sh restart
```

## Host-local daily operator path

### 1. Python environment

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install --upgrade pip==25.0.1
python3 -m pip install -e ".[dev]"
```

### 2. Configure the app

```bash
cp .env.example .env
```

At minimum, point the app at:

- a PostgreSQL database through `DATABASE_URL`
- an OpenSearch node through `OPENSEARCH_BASE_URL`
- either:
  - `SNAPSHOT_STORAGE_BACKEND=filesystem`
  - or `SNAPSHOT_STORAGE_BACKEND=minio` plus MinIO credentials

### 3. Start host services

Before migrating or starting the app, make sure the host-local dependencies you actually use are already running and reachable:

- PostgreSQL for `DATABASE_URL`
- OpenSearch for `OPENSEARCH_BASE_URL`
- MinIO for `MINIO_ENDPOINT` when `SNAPSHOT_STORAGE_BACKEND=minio`

Minimum reachability checks:

```bash
pg_isready
curl -fsS http://127.0.0.1:9200/
curl -fsS http://127.0.0.1:9000/minio/health/live
```

If you use the filesystem backend instead of MinIO, you can skip the MinIO check entirely.

### 4. Database migration

```bash
./scripts/migrate.sh upgrade head
```

### 5. Object-store bootstrap

Filesystem backend:

- no bucket bootstrap is required

MinIO backend:

```bash
python3 scripts/init_buckets.py
```

### 6. Index bootstrap

```bash
python3 scripts/init_index.py
```

### 7. Start the orchestrator

```bash
python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000
```

For normal owner-operated development, `./dev.sh restart` can replace steps 4 through 9
when the backing services in step 3 are already running. It runs migrations, initializes
MinIO buckets when `SNAPSHOT_STORAGE_BACKEND=minio`, initializes the OpenSearch index when
`INDEX_BACKEND=opensearch`, starts the backend, and starts the Vite frontend.

### 8. Health checks

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
curl -fsS http://127.0.0.1:8000/metrics
```

### 9. Web frontend for remote browser access

If the Linux server is accessed from a Mac over SSH, do not open the `localhost` URL printed in the server terminal directly from the Mac. That address is local to the Linux server. Prefer SSH port forwarding so the backend and Vite dev server can stay bound to `127.0.0.1`.

From the Mac, open a separate terminal:

```bash
ssh -N \
  -L 5173:127.0.0.1:5173 \
  -L 8000:127.0.0.1:8000 \
  user@server-host
```

On the Linux server:

```bash
python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000
cd apps/web
npm install
npm run dev
```

Then open `http://127.0.0.1:5173` on the Mac. Keep `VITE_API_BASE_URL=http://127.0.0.1:8000`; the SSH tunnel maps the Mac-side port `8000` to the Linux backend.

If you intentionally want direct server-IP access instead of a tunnel, bind both services to all interfaces and restrict ports `5173` and `8000` to your Mac's IP with host firewall or cloud security-group rules:

```bash
python3 -m uvicorn services.orchestrator.app.main:app --host 0.0.0.0 --port 8000
cd apps/web
VITE_API_BASE_URL=http://SERVER_IP:8000 npm run dev:remote
```

Then open `http://SERVER_IP:5173` on the Mac.

### 10. Real-dependency worker smoke

Use this path when PostgreSQL, MinIO, OpenSearch, SearXNG, orchestrator, worker, and web are all available.

1. Configure `.env` or the shell:

```bash
DATABASE_URL=postgresql+psycopg://deepsearch:deepsearch@127.0.0.1:5432/deepsearch
SNAPSHOT_STORAGE_BACKEND=minio
MINIO_ENDPOINT=127.0.0.1:9000
MINIO_ACCESS_KEY=<access-key>
MINIO_SECRET_KEY=<secret-key>
SNAPSHOT_STORAGE_BUCKET=snapshots
REPORT_STORAGE_BUCKET=reports
SEARCH_PROVIDER=searxng
SEARXNG_BASE_URL=http://127.0.0.1:8080
INDEX_BACKEND=opensearch
OPENSEARCH_BASE_URL=http://127.0.0.1:9200
OPENSEARCH_INDEX_NAME=source-chunks-v1
```

2. Initialize durable dependencies:

```bash
cd /share/zhuzy/projects/DeepSearch
./scripts/migrate.sh
python scripts/init_buckets.py
python scripts/init_index.py
```

3. Start orchestrator, worker, and web in separate shells:

```bash
PYTHONPATH=. python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000
PYTHONPATH=. python3 scripts/research_worker.py
cd apps/web && VITE_API_BASE_URL=http://127.0.0.1:8000 npm run dev -- --host 127.0.0.1
```

4. Run the worker-path smoke:

```bash
python3 scripts/smoke_planner_pipeline.py \
  --query "What is SearXNG and how does it work?" \
  --base-url http://127.0.0.1:8000
```

The smoke script creates a task, calls `/run`, waits for the host-local worker, checks
`source_documents`, `source_chunks`, `claims`, and the report artifact, then prints the task id
for frontend inspection at `http://127.0.0.1:5173/tasks/<task_id>`.

This closeout added the script/runbook alignment and unit coverage for the code path. A live
PostgreSQL + MinIO + OpenSearch + SearXNG stack still has to be started by the operator before
this real-dependency smoke can be truthfully marked as passed.

### 11. Synchronous API-chain smoke

If you have a reachable SearXNG-compatible endpoint:

```bash
python3 scripts/smoke_test.py --base-url http://127.0.0.1:8000
```

If you do not, use the repository-local deterministic mock:

```bash
python3 scripts/mock_searxng.py --host 127.0.0.1 --port 18080
export SEARXNG_BASE_URL=http://127.0.0.1:18080
python3 scripts/smoke_test.py --base-url http://127.0.0.1:8000
```

### 12. Development-only real pipeline debug run

For a host-local development smoke that drives one existing task through the real synchronous service chain without a worker:

```bash
curl -fsS -X POST \
  http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/debug/run-real-pipeline
```

This endpoint is available only when `APP_ENV=development`. It reuses the real search, fetch, parse, index, claim, verification, and Markdown report services. It does not mock external search or generate a fake report. It calls an LLM only when planner or grounded report-writer flags are explicitly enabled.

Required live dependencies:

- `SEARXNG_BASE_URL` must point at a reachable SearXNG-compatible endpoint
- selected search result URLs must be globally fetchable under the acquisition policy
- snapshot/report storage must be configured
- `OPENSEARCH_BASE_URL` and `OPENSEARCH_INDEX_NAME` must point at a reachable OpenSearch backend

On failure the response includes `stage`, `reason`, `exception`, `message`, `next_action`, and counts of any intermediate ledger rows already produced. The product `/run` endpoint only queues work; worker failures are visible later on the task detail/events APIs.

### 13. Worker-triggered full run

The frontend now calls the product run endpoint to enqueue work:

```bash
POST /api/v1/research/tasks/<task_id>/run
```

From the web UI:

1. open `/tasks` to inspect existing tasks, or `/tasks/new` to create a new task
2. enter a research query
3. generate/review the pre-run plan, then confirm it to queue `/run`
4. the UI navigates to task detail and polls progress/events while the worker runs
5. task detail exposes Run, Pause, Resume, and Cancel according to the current status
6. on completion the report page is available
7. on failure the task detail page shows `stage`, `reason`, `message`, `next_action`, stage events, and intermediate counts

Real-search mode requires:

```bash
SEARCH_PROVIDER=searxng
SEARXNG_BASE_URL=http://<searxng-host>:<port>
INDEX_BACKEND=opensearch
OPENSEARCH_BASE_URL=http://127.0.0.1:9200
python3 scripts/init_index.py
python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000
PYTHONPATH=. python scripts/research_worker.py
```

If `SEARXNG_BASE_URL=http://127.0.0.1:8080` returns frontend HTML, it is misconfigured. Point it at a SearXNG-compatible `/search?format=json` endpoint, or use explicit smoke mode for development validation.

For local web UI testing without a real SearXNG endpoint, use the deterministic smoke path and
create a new task after any failed run:

```bash
SEARCH_PROVIDER=smoke INDEX_BACKEND=local SNAPSHOT_STORAGE_BACKEND=filesystem ./dev.sh restart
```

Failed tasks are kept as audit records and are not run again in place. The task detail page can
create a replacement task with the same query so the old failure remains inspectable.

The SearXNG client validates endpoint responses before storing search ledger rows. Common structured failures:

- `searxng_html_response`: the configured endpoint returned HTML instead of JSON
- `searxng_http_forbidden`: the endpoint returned HTTP 403, often due to access rules, CAPTCHA, or rate limiting
- `searxng_invalid_json`: the endpoint returned a non-JSON body
- `searxng_empty_results_with_unresponsive_engines`: SearXNG returned no results and reported unavailable engines

For each SearXNG response, logs include `SEARCH_PROVIDER`, `SEARXNG_BASE_URL`, status, content type, the first 300 response characters, and `unresponsive_engines`.

Development smoke mode:

```bash
APP_ENV=development \
SEARCH_PROVIDER=smoke \
INDEX_BACKEND=local \
python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000
```

This mode still performs real HTTP acquisition of `https://example.com/`, real parsing, real ledger persistence, deterministic local indexing, deterministic claim/evidence generation, and Markdown report generation. It does not perform real web search, does not use OpenSearch, and does not call an LLM unless planner or grounded report-writer flags are explicitly enabled.

## Host-local validation commands

```bash
python3 -m ruff check .
python3 -m black --check .
python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit
python3 -m pytest
./scripts/migrate.sh current
python3 scripts/init_index.py
python3 scripts/smoke_test.py --base-url http://127.0.0.1:8000
```

For smoke/local validation without OpenSearch:

```bash
APP_ENV=development SEARCH_PROVIDER=smoke INDEX_BACKEND=local \
  python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000
```

Then use the frontend `Create And Run DeepSearch` button or call:

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/run
```

If you use MinIO instead of the filesystem backend, also run:

```bash
python3 scripts/init_buckets.py
```

## Acquisition policy

| Boundary | Current rule |
| --- | --- |
| Allowed schemes | `http`, `https` |
| Blocked hostnames | `localhost`, `metadata`, `metadata.google.internal` |
| Blocked resolved targets | loopback, private, link-local, and other non-global IPs |
| Request timeout | `10` seconds by default |
| Redirect limit | `3` by default |
| Max response bytes | `1048576` by default |

## Optional Docker / compose tooling

The repository still contains:

- `docker-compose.yml`
- `docker-compose.dev.yml`
- `.env.compose.example`

Current status:

- they are optional packaging only
- they are not the primary recommended path
- they were not re-established as the acceptance gate in this closeout
- on the current validation host, `docker` is unavailable, so compose runtime was not re-run in this closeout turn

If you choose to use compose later, treat it as operator convenience rather than the canonical route.

## Real dependency integration status

The current codebase has already been validated on the host-local path against:

- real PostgreSQL
- real OpenSearch
- real MinIO
- orchestrator startup with those dependencies configured
- end-to-end smoke through `task -> search -> fetch -> parse -> index -> draft -> verify -> report`

The current closeout does not claim that Docker compose has been fully re-run on this host.

## If the project later returns to a reproducible-deployment route

At minimum, that route would still need:

- Docker / compose runtime validation on a host that actually has `docker`
- clearer packaging of OpenSearch certificates and security bootstrap
- a stronger documented startup and recovery story for compose-managed services
- explicit validation that compose does not become the only supported operator path

## Troubleshooting

### `./dev.sh restart` refuses to start because a port is occupied

- run `./dev.sh status` to see PID files, expected commands, URLs, and port listeners
- if the listener is a managed DeepSearch process, `./dev.sh stop` should remove it
- if the listener is unrelated, either stop that process or choose `DEV_BACKEND_PORT` /
  `DEV_FRONTEND_PORT`
- the helper intentionally does not kill arbitrary processes that merely occupy port
  `8000` or `5173`

### `./dev.sh restart` fails during migration, bucket init, or index init

- inspect `.logs/backend.log` only after backend startup has begun; init failures usually
  print directly in the terminal
- run the narrower step with `./dev.sh init` after fixing `DATABASE_URL`, MinIO, or
  OpenSearch settings
- for deterministic development without OpenSearch, use
  `SEARCH_PROVIDER=smoke INDEX_BACKEND=local ./dev.sh restart`
- to skip all init steps temporarily, use `DEV_RUN_INIT=false ./dev.sh restart`

### `./dev.sh stop` leaves a process behind

- new runs are started in their own process group, so repeated `./dev.sh stop` should be
  idempotent
- if the process was started by an older script version or by hand, check
  `./dev.sh status` and stop the remaining PID manually only after confirming the command
  line belongs to this checkout

### App fails at startup with `unsupported snapshot storage backend`

- use only `filesystem` or `minio`
- if using `minio`, check endpoint, credentials, and buckets

### App fails at startup with `unsupported index backend`

- the current code only supports `opensearch`

### App fails at startup with `opensearch username and password must be configured together`

- either set both `OPENSEARCH_USERNAME` and `OPENSEARCH_PASSWORD`, or leave both empty

### App fails at startup with `opensearch CA bundle does not exist`

- either provide a real CA bundle path
- or, for a local non-TLS path, set `OPENSEARCH_VERIFY_TLS=false` and leave `OPENSEARCH_CA_BUNDLE_PATH` empty

### `scripts/init_buckets.py` fails

- check `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, and `MINIO_SECRET_KEY`
- if `MINIO_ENDPOINT` includes `http://` or `https://`, the script derives TLS mode from the scheme

### `scripts/init_index.py` fails

- check OpenSearch reachability and auth
- check `OPENSEARCH_VERIFY_TLS` and `OPENSEARCH_CA_BUNDLE_PATH`

### Smoke test fails at search

- verify `SEARXNG_BASE_URL`
- if no external SearXNG is available, use `scripts/mock_searxng.py`
- inspect the structured search failure `reason` and JSON logs; HTML, 403, invalid JSON, and empty results with unresponsive engines are reported explicitly

### Mac browser cannot open the frontend URL printed by the Linux server

- if the URL is `http://localhost:5173` or `http://127.0.0.1:5173`, it is local to the Linux server, not the Mac
- preferred fix: use SSH forwarding for both frontend and backend with `ssh -L 5173:127.0.0.1:5173 -L 8000:127.0.0.1:8000 user@server-host`
- alternative fix: run the frontend with `npm run dev:remote`, run the backend with `--host 0.0.0.0`, set `VITE_API_BASE_URL=http://SERVER_IP:8000`, and open the required firewall ports only to your Mac
- if the frontend loads but API calls fail, check whether `VITE_API_BASE_URL` points to the browser machine instead of the Linux server or SSH tunnel

### Smoke test fails at fetch

- verify the returned URLs are globally reachable
- the current acquisition policy intentionally blocks loopback, private, link-local, and metadata targets
- mixed DNS answers are allowed when at least one resolved IP is global; inspect fetch attempt trace fields `allowed_ips`, `blocked_ips`, and `decision_reason` before treating `non_global_ip` as a domain-wide block
- inspect task events or `GET /api/v1/research/tasks/<task_id>` for `progress.observability.failed_sources`; failed entries include URL, HTTP status, error code, and error reason when the fetch trace recorded one
- a run with one successful fetched source and one or more failed sources can still complete; it emits a warning that report coverage may be weak

### Smoke test fails at parse

- supported MIME types are `text/html`, `text/plain`, safe raw text formats such as Markdown/YAML/env,
  `application/pdf`, DOCX, PPTX, and XLSX OpenXML documents
- inspect task events or `GET /api/v1/research/tasks/<task_id>` for `progress.observability.parse_decisions`
- parse decisions include `snapshot_id`, `canonical_url`, `mime_type`, `storage_bucket`, `storage_key`, `snapshot_bytes`, `body_length`, `decision`, `parser_error`, `extractor_strategy_used`, `fallback_used`, `removed_boilerplate_count`, `extracted_text_length`, `text_cleanup_applied`, `dropped_broken_link_fragments`, and `preserved_link_text_count`
- for Wikipedia/MediaWiki pages, expected extraction is article-body text from `main`, `article`, `#content`, `#bodyContent`, `#mw-content-text`, or `.mw-parser-output`, with paragraph fallback from `.mw-parser-output p`, `#mw-content-text p`, or readable body paragraphs if strict extraction would otherwise be empty
- for Sphinx docs pages, link text should be preserved when present; if a docs page still produces broken residue such as `from up to 251 .`, the extractor applies conservative cleanup and records cleanup metadata rather than fabricating missing content
- a chunk that starts with `References` should remain ineligible, but a chunk with useful `Privacy` prose followed by trailing `See also` or `References` headings should remain eligible when its quality score passes
- `skipped_empty`, `missing_blob`, `skipped_unsupported_mime`, and `parse_error` are distinct outcomes; a `PARSING` failure message lists these per snapshot instead of only reporting that no source documents were produced
- `POST /api/v1/research/tasks/<task_id>/parse` remains status-gated; a FAILED task returns `409` and should be revised or recreated rather than rerun in place

### Smoke test fails at index, draft, verify, or report

- inspect `GET /metrics`
- inspect JSON logs from the orchestrator
- if `DRAFTING_CLAIMS` fails with `claim drafting produced no claims`, inspect `pipeline.failed.details`; it should include `why_supplemental_acquisition_triggered`, `supplemental_sources_attempted`, `supplemental_sources_skipped`, `unattempted_high_quality_sources`, `why_wikipedia_or_about_not_attempted`, `per_source_answer_yield`, `top_rejected_candidates`, `rejection_reason_distribution`, and an operator `next_action`
- if a planner-enabled overview task only attempted low-yield home/developer pages, inspect `progress.observability.final_search_queries`, the Source Selection Table in Task Detail, and candidate metadata `source_category` / `downrank_reason`; official about and Wikipedia candidates should outrank `dev/index`, API, install, and architecture pages unless the query asks for those topics
- if a technical concept task attempts generic tutorial domains before official docs/reference/GitHub, inspect `progress.observability.selected_sources_from_search` and `unattempted_sources`; generic `What is ...` pages should now stay `generic_article`, localized mirrors should stay `secondary_reference`, third-party GitHub repositories should not be `github_readme_or_repo`, off-subject official docs should show `off_subject_source_downranked_for_query`, and gap rounds should fall back to unattempted high-value candidates when new search results are low-value or duplicates
- if planner-LLM mode reaches `RESEARCHING_MORE` and SearXNG reports empty results with unresponsive engines, inspect `progress.observability.gap_rounds`; tasks with existing usable documents/chunks/claims should continue to reporting with `gap_search_unavailable` / `supplemental_search_failed` warnings, while tasks with no usable evidence should still fail at the main failing stage
- if planner-LLM mode fails in main `SEARCHING` with `searxng_empty_results_with_unresponsive_engines`, inspect `progress.observability.search_queries`, `progress.observability.known_path_fallback`, and the `SEARCHING` stage event; known LangGraph overview tasks should show fallback injection and proceed to acquisition, while unknown entities should fail clearly with query count, empty-query count, provider error type, and the first sanitized failed query
- if task detail appears to show `fetch_succeeded = 0` while source summaries show fetched/parsed sources, inspect `progress.observability.gap_rounds`; task-level `fetch_succeeded` and `fetch_failed` are cumulative over initial acquisition plus gap-round acquisition, while each gap round still carries its own per-round acquisition counters
- query the intermediate resources:
  - `GET /candidate-urls`
  - `GET /content-snapshots`
  - `GET /source-documents`
  - `GET /source-chunks`
  - `GET /indexed-chunks`
  - `GET /claims`
  - `GET /claim-evidence`

### Pause or cancel appears to be ignored while the worker is running

- expected behavior is stage-boundary control: `/pause` or `/cancel` records the requested
  status immediately, and the worker must stop before starting the next pipeline stage
- if a task briefly returns `PAUSED` but then advances to the next runtime status such as
  `ACQUIRING`, restart the backend and worker from the current checkout; older worker code may
  have cached the task row and missed the external control update
- inspect `GET /api/v1/research/tasks/<task_id>/events`; a correct stage-boundary pause has
  `task.paused` after a `pipeline.stage_started` event and no later
  `pipeline.stage_started` event until `/resume`
- after `/resume`, the task returns to `QUEUED` and the worker may continue from the latest
  checkpoint; `/cancel` from `QUEUED` or an active runtime status should leave the task in
  `CANCELLED`
