# Implementation Audit - 2026-04-24

Scope: audit the current implementation state of search, acquisition, parsing, indexing, LLM configuration, report generation, and database migrations. This document records observed code reality only. It does not introduce worker execution, LangGraph wiring, mocks as production behavior, or new architecture.

Current correction as of 2026-04-28: this audit is historical. The codebase now includes a
planner-only LLM provider seam under `services/orchestrator/app/llm/`, a Research Planner v1
under `services/orchestrator/app/planning/`, and a deterministic quality layer under
`services/orchestrator/app/research_quality/`. The LLM boundary remains planner-only; claim
drafting, verification, and report generation are still deterministic and evidence-backed.

## Summary

The current codebase has a real synchronous host-local pipeline through:

`research_task -> search -> fetch -> parse -> index -> draft -> verify -> report`

The implemented path is not background worker based. Claim drafting and verification are deterministic heuristics over stored chunks and OpenSearch retrieval. LLM use is now limited to optional research planning.

Current local development environment status on this machine:

- orchestrator liveness and readiness are reachable on `http://127.0.0.1:8000`
- Alembic reports current revision `20260424_0005 (head)` for the configured SQLite dev database
- `SEARXNG_BASE_URL=http://127.0.0.1:8080` currently returns frontend HTML, not SearXNG JSON
- `OPENSEARCH_BASE_URL=http://127.0.0.1:9200` is not reachable
- `scripts/init_index.py` fails because OpenSearch is not running

## Capability Matrix

| Capability | Real implementation? | Stub/mock/empty shell? | Current local dev runnable? | Minimum blocker | Next priority |
| --- | --- | --- | --- | --- | --- |
| Search service | Yes, via real SearXNG-compatible HTTP JSON request and DB persistence | Not a stub. `scripts/mock_searxng.py` is dev-only smoke helper | Not with current default endpoint; port `8080` returns frontend HTML | Point `SEARXNG_BASE_URL` at real SearXNG or run the dev mock intentionally | Fix local search endpoint configuration and document the expected `/search?format=json` response |
| Web acquisition | Yes, via policy-guarded real HTTP GET, redirect handling, DNS/IP checks, and snapshot storage | Not a stub | Code path is runnable, but depends on candidate URLs from search and globally reachable URLs | Search must produce allowed public URLs; target URLs must pass SSRF policy | After search endpoint is fixed, run fetch smoke against a known public HTML/plain-text URL |
| Parsing | Yes, for stored `text/html` and `text/plain` snapshots | Not a stub, but intentionally minimal; no Tika/PDF/Office parser | Runnable after successful fetch creates snapshots | Needs successful content snapshots; unsupported MIME types are skipped | Keep as-is for current phase; next improvement would be clearer operator fixture for HTML/plain parsing |
| OpenSearch/index backend | Yes, real REST backend creates index, upserts chunks, lists and retrieves by task | Not a stub | Not currently runnable because `127.0.0.1:9200` refuses connection | Start/configure OpenSearch and run `scripts/init_index.py` | Bring up OpenSearch host-local and validate `init_index.py`, `/index`, `/retrieve` |
| LLM API key/client | Planner-only provider seam exists | No LLM claim/report path; noop and OpenAI-compatible planner providers exist | Runnable when `LLM_ENABLED=true` and `RESEARCH_PLANNER_ENABLED=true` with valid provider config | Missing or invalid provider config falls back only at planner execution boundaries | Keep LLM scoped to planning until evidence quality and verification are stronger |
| Report generation service | Yes, deterministic Markdown synthesis from persisted claims/evidence and object storage | Not a stub; no external dedicated reporter service | Runnable after claims/evidence exist; can also generate empty-ledger Markdown for an existing task | Needs object store configuration and useful claim/evidence ledger for meaningful output | After index/search are fixed, validate full `draft -> verify -> report` smoke |
| Database tables/migrations | Mostly complete for current Phase 11 synchronous loop | Not a stub | Current dev DB is at head | No worker/job tables beyond fetch job; no parse/index/report job ledgers by design | Keep schema frozen unless implementing a real worker or richer provenance requires migration |

## Code Map

### Search

- `services/orchestrator/app/search/providers.py`
- `services/orchestrator/app/search/canonicalization.py`
- `services/orchestrator/app/search/query_expansion.py`
- `services/orchestrator/app/services/search_discovery.py`
- `services/orchestrator/app/api/routes/search_discovery.py`
- `packages/db/repositories/search.py`
- tables: `research_task`, `research_run`, `search_query`, `candidate_url`
- env vars: `SEARXNG_BASE_URL`, `SEARXNG_TIMEOUT_SECONDS`, `SEARCH_MAX_RESULTS_PER_QUERY`, `QUERY_EXPANSION_MAX_DOMAINS`
- external services: SearXNG-compatible HTTP endpoint

### Acquisition

- `services/orchestrator/app/acquisition/http_client.py`
- `services/orchestrator/app/services/acquisition.py`
- `services/orchestrator/app/api/routes/acquisition.py`
- `services/orchestrator/app/storage/snapshots.py`
- `packages/db/repositories/fetch.py`
- tables: `research_task`, `candidate_url`, `fetch_job`, `fetch_attempt`, `content_snapshot`
- env vars: `ACQUISITION_TIMEOUT_SECONDS`, `ACQUISITION_MAX_REDIRECTS`, `ACQUISITION_MAX_RESPONSE_BYTES`, `ACQUISITION_MAX_CANDIDATES_PER_REQUEST`, `ACQUISITION_USER_AGENT`, storage env vars
- external services: public HTTP/HTTPS targets; filesystem or MinIO object storage

### Parsing

- `services/orchestrator/app/parsing/extractors.py`
- `services/orchestrator/app/parsing/chunking.py`
- `services/orchestrator/app/services/parsing.py`
- `services/orchestrator/app/api/routes/parsing.py`
- `packages/db/repositories/sources.py`
- tables: `research_task`, `fetch_job`, `fetch_attempt`, `content_snapshot`, `source_document`, `source_chunk`
- env vars: storage env vars
- external services: filesystem or MinIO object storage

### Indexing/Retrieval

- `services/orchestrator/app/indexing/backends.py`
- `services/orchestrator/app/services/indexing.py`
- `services/orchestrator/app/api/routes/indexing.py`
- `scripts/init_index.py`
- tables: `research_task`, `source_document`, `source_chunk`; index data is external to the relational DB
- env vars: `INDEX_BACKEND`, `OPENSEARCH_BASE_URL`, `OPENSEARCH_INDEX_NAME`, `OPENSEARCH_USERNAME`, `OPENSEARCH_PASSWORD`, `OPENSEARCH_VERIFY_TLS`, `OPENSEARCH_CA_BUNDLE_PATH`, `OPENSEARCH_TIMEOUT_SECONDS`, `OPENSEARCH_VALIDATE_CONNECTIVITY_ON_STARTUP`, `INDEXING_MAX_CHUNKS_PER_REQUEST`, `RETRIEVAL_MAX_RESULTS_PER_REQUEST`
- external services: OpenSearch

### LLM

- `services/orchestrator/app/llm/providers.py`
- `services/orchestrator/app/llm/client.py`
- `services/orchestrator/app/planning/planner.py`
- providers: noop and OpenAI-compatible aliases
- env vars: `LLM_ENABLED`, `LLM_PROVIDER`, `LLM_MODEL`, `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES`, `LLM_MAX_OUTPUT_TOKENS`, `RESEARCH_PLANNER_ENABLED`, `RESEARCH_PLANNER_MAX_SUBQUESTIONS`, `RESEARCH_PLANNER_MAX_SEARCH_QUERIES`
- external services: optional OpenAI-compatible chat completions endpoint
- database tables: none; planner output is persisted through task events
- boundary: planner-only; no LLM-written claims, verification decisions, or reports

### Reporting

- `services/orchestrator/app/reporting/markdown.py`
- `services/orchestrator/app/reporting/manifest.py`
- `services/orchestrator/app/services/reporting.py`
- `services/orchestrator/app/api/routes/reporting.py`
- `packages/db/repositories/reports.py`
- tables: `research_task`, `claim`, `claim_evidence`, `citation_span`, `source_chunk`, `source_document`, `report_artifact`
- env vars: `SNAPSHOT_STORAGE_BACKEND`, `SNAPSHOT_STORAGE_ROOT`, `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_SECURE`, `MINIO_REGION`, `REPORT_STORAGE_BUCKET`
- external services: filesystem or MinIO object storage

### Database/Migrations

- `packages/db/models/ledger.py`
- `packages/db/models/constants.py`
- `packages/db/repositories/*.py`
- `migrations/versions/20260422_0001_initial_ledger_schema.py`
- `migrations/versions/20260423_0002_task_revision_and_event_sequence.py`
- `migrations/versions/20260423_0003_fetch_job_candidate_mode_uniqueness.py`
- `migrations/versions/20260423_0004_source_document_snapshot_provenance.py`
- `migrations/versions/20260424_0005_report_artifact_manifest_and_hash.py`
- tables covered: `research_task`, `research_run`, `task_event`, `search_query`, `candidate_url`, `fetch_job`, `fetch_attempt`, `content_snapshot`, `source_document`, `source_chunk`, `citation_span`, `claim`, `claim_evidence`, `report_artifact`

## Validation Run

- `python3 -m pytest ... -q` for targeted search, acquisition, parsing, indexing, reporting, migration, and repository tests: passed, 58 tests
- `python3 -m ruff check ...`: passed
- LLM status in this historical validation is stale; current code has planner-only LLM references and tests
- `python3 - <<'PY' ... create_app() ... PY`: passed; startup validation succeeds with filesystem storage and OpenSearch live validation disabled
- `curl -fsS --max-time 2 http://127.0.0.1:8000/healthz`: passed
- `curl -fsS --max-time 2 http://127.0.0.1:8000/readyz`: passed
- `./scripts/migrate.sh current`: passed; current revision is `20260424_0005 (head)`
- `curl -fsS --max-time 2 http://127.0.0.1:8080/search?q=example&format=json`: returned frontend HTML, not SearXNG JSON
- `curl -fsS --max-time 2 http://127.0.0.1:9200/`: failed; connection refused
- `python3 scripts/init_index.py`: failed because OpenSearch connection to `127.0.0.1:9200` was refused

## Deferred Work

- no worker implementation was audited as runnable because worker execution is not implemented
- no LangGraph runtime exists in the current codebase
- no LLM claim drafting, verification, or report-writing path exists
- no Tika/PDF/Office parsing exists
- no HTML/PDF report export exists
- no parse-job, index-job, or report-job ledger tables exist; current phase uses synchronous endpoint/service execution
