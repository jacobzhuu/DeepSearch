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
- support-only claim drafting with citation span binding
- minimal claim verification with `support` and `contradict` evidence
- Markdown report synthesis backed by persisted report artifacts
- task-event and task-detail observability for search result counts, selected sources, fetch success/failure counts, failed fetch reasons, parse decisions, and low-source warnings
- report page HTML rendering plus Raw Markdown, Copy Markdown, and Download `.md` controls
- JSON logs and basic metrics

## What is intentionally not being expanded now

- OpenClaw
- HTML export
- PDF export
- planner / gap analyzer
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

- `SEARCH_PROVIDER=smoke` returns a clearly marked smoke search result for `https://example.com/`; it is not real search
- `INDEX_BACKEND=local` uses an in-process deterministic index; it is not durable and is not a replacement for OpenSearch
- together these report `running_mode=smoke-search+deterministic-local+no-LLM`

### Claims and report

| Variable | Purpose | Default |
| --- | --- | --- |
| `CLAIM_DRAFTING_MAX_CANDIDATES_PER_REQUEST` | Max retrieval candidates for drafting | `5` |
| `CLAIM_VERIFICATION_MAX_CLAIMS_PER_REQUEST` | Max claims per verification request | `5` |

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
- parse decisions include `snapshot_id`, `canonical_url`, `mime_type`, `storage_bucket`, `storage_key`, `snapshot_bytes`, `body_length`, `decision`, and `parser_error`
- `skipped_empty`, `missing_blob`, `skipped_unsupported_mime`, and `parse_error` are distinct outcomes; a `PARSING` failure message lists these per snapshot instead of only reporting that no source documents were produced
- `POST /api/v1/research/tasks/<task_id>/parse` remains status-gated; a FAILED task returns `409` and should be revised or recreated rather than rerun in place

### Smoke test fails at index, draft, verify, or report

- inspect `GET /metrics`
- inspect JSON logs from the orchestrator
- query the intermediate resources:
  - `GET /candidate-urls`
  - `GET /content-snapshots`
  - `GET /source-documents`
  - `GET /source-chunks`
  - `GET /indexed-chunks`
  - `GET /claims`
  - `GET /claim-evidence`
