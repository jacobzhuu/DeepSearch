# Runbook

## Current project positioning

- this is now a single-operator, self-hosted research platform
- the recommended runtime path is host-local Linux
- the current completed functional loop is:
  - `task -> search -> fetch -> parse -> index -> draft -> verify -> report`
- Docker and compose may stay in the repository as optional tooling, but they are not the primary route or acceptance standard
- no new product features are being added in this closeout beyond conflict fixes required to keep the current path stable

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
- synchronous search discovery with canonicalized candidate URLs
- synchronous HTTP acquisition with fetch jobs, attempts, and stored snapshots
- synchronous parsing for `text/html` and `text/plain`
- task-scoped chunk indexing and retrieval through OpenSearch
- support-only claim drafting with citation span binding, query-aware deterministic claim scoring, and answer-focused top-K selection
- minimal claim verification with `support` and `contradict` evidence
- Markdown report synthesis backed by persisted report artifacts, with low-quality/off-query claim filtering and low answer-coverage warnings
- shared deterministic source-intent, answer-slot, evidence-candidate, source-yield, evidence-yield, dropped-source reason, and slot-coverage contracts for source selection, diagnostics, verification, and report coverage
- task-event and task-detail observability for planner guardrails, final search queries, source selection, answer slots, source yield, evidence yield, slot coverage, answer yield, answer coverage, verifier strong/weak support counts, supplemental acquisition, fetch success/failure counts, failed fetch reasons, parse decisions, and actionable failure diagnostics
- report page HTML rendering plus Raw Markdown, Copy Markdown, and Download `.md` controls
- JSON logs and basic metrics

## What is intentionally not being expanded now

- OpenClaw
- HTML export
- PDF export
- multi-round planner / gap analyzer
- complex verifier logic
- complex retrieval optimization
- new worker or background execution semantics

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
| `ACQUISITION_USER_AGENT` | Acquisition user agent | `deepresearch-orchestrator/0.1` |

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

### Claims and report

| Variable | Purpose | Default |
| --- | --- | --- |
| `CLAIM_DRAFTING_MAX_CANDIDATES_PER_REQUEST` | Max retrieval candidates for drafting | `5` |
| `CLAIM_VERIFICATION_MAX_CLAIMS_PER_REQUEST` | Max claims per verification request | `5` |

Claim drafting is deterministic and no-LLM. For definition/mechanism queries such as `What is SearXNG and how does it work?`, the selector now assigns an `answer_role` and prefers definition, mechanism, privacy/design-goal, feature, and low-priority deployment/self-hosting sentences. It rejects contribution calls-to-action, community logistics, documentation pointers, promotional slogans, lowercase fragments, setup/getting-started instructions, diagram/config fragments, and broken-link residue such as `listed at .` before claim persistence. Scoring metadata is stored in `claim.notes_json` and regenerated reports use that metadata to exclude low-quality, setup, unsupported-category, or off-query supported claims from the report body.

Evidence-quality metadata is a code-level contract, not a new table. Claim notes and task/report diagnostics may include `evidence_candidate_id`, `slot_ids`, `source_intent`, citation span ids, claim evidence ids, source-yield rows, evidence-yield summaries, and slot-coverage summaries. Dropped-source reasons use this taxonomy: `not_selected_low_priority`, `blocked_by_policy`, `fetch_failed`, `unsupported_content_type`, `parse_failed`, `low_chunk_quality`, `no_evidence_candidates`, `evidence_rejected`, `duplicate_or_near_duplicate`, `off_intent`, and `unknown`.

Backward compatibility note: old tasks and report artifacts may not have the newer diagnostics payloads. The API and benchmark script normalize missing `source_yield_summary`, `dropped_sources`, and `slot_coverage_summary` to `[]`, and missing `evidence_yield_summary` or `verification_summary` to `{}` whenever an observability payload exists. Older claim notes without `evidence_candidate_id` remain reportable through their persisted citation spans.

Verification is deterministic lexical verification, not full entailment. It persists only the existing `supported`, `mixed`, and `unsupported` statuses, but records strong support, weak support, contradiction, shallow-overlap, numeric/date mismatch, and scope-mismatch details in `claim.notes_json["verification"]`. Reports keep weak lexical support out of the main answer sections.

If strict claim filters produce no claims, the service runs a narrow deterministic fallback over explanatory definition, mechanism, privacy, or feature sentences only. It does not promote short slogans such as `Search without being tracked.`, contribution/community text, navigation, references, redirect stubs, or setup-only instructions unless the query explicitly asks for that material. Fallback claims are marked in `claim.notes_json` with `draft_mode = "fallback_relaxed"`, `fallback_reason`, and `original_rejected_reason`.

The pipeline also computes answer-yield metrics per `source_document`, separating raw `candidate_sentence_count` from `answer_relevant_candidate_count` and final accepted claim counts. If claim drafting creates zero claims, or a what/how query produces only one claim without required definition/mechanism coverage, it runs at most one supplemental acquisition pass over unattempted high-value candidates such as official about pages, Wikipedia references, official home pages, GitHub README/repo pages, or generic articles. Developer, API, architecture, installation, social, forum, and video pages stay downranked unless the query explicitly asks for them.

### Optional LLM planner

| Variable | Purpose | Default |
| --- | --- | --- |
| `LLM_ENABLED` | Master switch for planner LLM provider construction | `false` |
| `LLM_PROVIDER` | `noop` or OpenAI-compatible aliases: `openai-compatible`, `openai_compatible`, `openai` | `noop` |
| `LLM_MODEL` | Provider model name | empty |
| `LLM_API_KEY` | Provider API key from env or `.env`; never log or expose this value | empty |
| `LLM_BASE_URL` | OpenAI-compatible API base URL, usually ending in `/v1` | empty |
| `LLM_TIMEOUT_SECONDS` | LLM HTTP timeout | `30` |
| `LLM_MAX_RETRIES` | Retry count for retryable LLM provider errors | `1` |
| `LLM_MAX_OUTPUT_TOKENS` | Planner response token cap | `1200` |
| `RESEARCH_PLANNER_ENABLED` | Run planner before search when `LLM_ENABLED=true` | `false` |
| `RESEARCH_PLANNER_MAX_SUBQUESTIONS` | Planner subquestion cap | `5` |
| `RESEARCH_PLANNER_MAX_SEARCH_QUERIES` | Planner search-query cap | `8` |

The planner is disabled by default. When enabled, it emits `research_plan.created` before `SEARCHING` and lets search discovery use capped, deduped planner queries plus the original query. If planner generation fails, it emits `research_plan.failed`, falls back to the original query, and continues the deterministic pipeline.

For definition and overview queries such as `What is SearXNG and how does it work?`,
planner output is post-processed as a bounded suggestion. Wikipedia cannot be treated as a
hard avoid domain for that query type. Final query planning always preserves the original
query plus official documentation, about/how-it-works, Wikipedia, and software-project
GitHub README searches when an entity can be extracted. SearXNG overview runs also add
deterministic known-path candidates for `https://docs.searxng.org/user/about.html` and
`https://en.wikipedia.org/wiki/SearXNG` when official SearXNG search results are present.
Source selection prioritizes official about/reference pages over admin architecture,
installation, API, or developer pages unless the user explicitly asks about those topics.

The planner never writes claims or final reports. Claim drafting, evidence binding, verification, and Markdown report synthesis remain deterministic and source-backed.

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
```

DeepSeek planner smoke test:

```bash
source /root/anaconda3/etc/profile.d/conda.sh
conda activate deepsearch311
cd /share/zhuzy/projects/DeepSearch
python scripts/smoke_deepseek_planner.py
```

The script reads `.env`, calls only the planner/provider layer, and does not create a task or
run the full pipeline. It prints provider, model, intent, subquestions, search queries, and
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

3. Create a task and run it:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/research/tasks \
  -H "Content-Type: application/json" \
  -d '{"query":"What is SearXNG and how does it work?","constraints":{}}' \
  | python3 -m json.tool

TASK_ID=<task_id from the create response>

curl -s -X POST http://127.0.0.1:8000/api/v1/research/tasks/$TASK_ID/run \
  | python3 -m json.tool
```

4. Check planner events:

```bash
curl -s http://127.0.0.1:8000/api/v1/research/tasks/$TASK_ID/events \
  | python3 -m json.tool \
  | grep -E "research_plan|planner|subquestions|search_queries" -n
```

5. Check the deterministic report:

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

The helper reads `.env`, creates a task, runs `POST /api/v1/research/tasks/<task_id>/run`,
and prints task id, status, running mode, planner status, final search queries, attempted
sources, source documents, chunk count, claims by category, report preview, and failure
details. It never prints API keys. Exit code `0` means the pipeline completed with at least
three claims and a report; `1` means the pipeline failed or produced an insufficient report;
`2` means the service or configuration is unavailable.

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
source-yield, evidence-yield, verifier, and report-structure generalization, not a golden-output
text comparison. With `--run --json`, each row includes slot coverage, source yield, evidence
yield, verification summary, and a non-SearXNG contamination check.

Useful benchmark narrowing options:

```bash
python scripts/benchmark_queries.py --json --limit 2
python scripts/benchmark_queries.py --json --query-id 3
python scripts/benchmark_queries.py --run --limit 2 --output /tmp/deepsearch-benchmark.json
```

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

### 10. Smoke test

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

### 11. Development-only real pipeline debug run

For a host-local development smoke that drives one existing task through the real synchronous service chain without a worker:

```bash
curl -fsS -X POST \
  http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/debug/run-real-pipeline
```

This endpoint is available only when `APP_ENV=development`. It reuses the real search, fetch, parse, index, claim, verification, and Markdown report services. It does not mock external search, does not call an LLM, and does not generate a fake report.

Required live dependencies:

- `SEARXNG_BASE_URL` must point at a reachable SearXNG-compatible endpoint
- selected search result URLs must be globally fetchable under the acquisition policy
- snapshot/report storage must be configured
- `OPENSEARCH_BASE_URL` and `OPENSEARCH_INDEX_NAME` must point at a reachable OpenSearch backend

On failure the response includes `stage`, `reason`, `exception`, `message`, `next_action`, and counts of any intermediate ledger rows already produced. The product `/run` endpoint also moves the task to `FAILED` for inspection.

### 12. Frontend-triggered full run

The frontend now calls the product run endpoint:

```bash
POST /api/v1/research/tasks/<task_id>/run
```

From the web UI:

1. open `/tasks/new`
2. enter a research query
3. click `Create And Run DeepSearch`
4. on success the UI navigates to the report page
5. on failure the task detail page shows `failed_stage`, `reason`, `message`, `next_action`, stage events, and intermediate counts

Real-search mode requires:

```bash
SEARCH_PROVIDER=searxng
SEARXNG_BASE_URL=http://<searxng-host>:<port>
INDEX_BACKEND=opensearch
OPENSEARCH_BASE_URL=http://127.0.0.1:9200
python3 scripts/init_index.py
python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000
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

This mode still performs real HTTP acquisition of `https://example.com/`, real parsing, real ledger persistence, deterministic local indexing, deterministic claim/evidence generation, and Markdown report generation. It does not perform real web search, does not use OpenSearch, and does not call an LLM.

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

- only `text/html` and `text/plain` are supported
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
- query the intermediate resources:
  - `GET /candidate-urls`
  - `GET /content-snapshots`
  - `GET /source-documents`
  - `GET /source-chunks`
  - `GET /indexed-chunks`
  - `GET /claims`
  - `GET /claim-evidence`
