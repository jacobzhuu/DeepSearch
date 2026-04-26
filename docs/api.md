# API

## Phase 11 endpoints

Phase 11 keeps the existing system endpoints, the Phase 2 thin research task API, the Phase 3 search-discovery endpoints, the Phase 4 acquisition endpoints, the Phase 5 parsing endpoints, the Phase 6 indexing endpoints, the Phase 7 claim-drafting endpoints, the Phase 8 verification endpoints, and the Phase 9 report-synthesis endpoints. No new product API semantics are introduced in this phase. The current operator route is host-local / self-hosted Linux first; optional Docker or compose packaging does not change any API contract.

### `GET /healthz`

Purpose: process liveness for the minimal FastAPI service.

Response `200 OK`:

```json
{
  "status": "ok"
}
```

### `GET /readyz`

Purpose: basic readiness for the Phase 10 service shell.

Response `200 OK`:

```json
{
  "environment": "development",
  "service": "deepresearch-orchestrator",
  "status": "ready"
}
```

### `GET /metrics`

Purpose: expose the minimum Prometheus-style metrics payload for the current process.

Read contract:

- intended as an operator or debug endpoint, not a product workflow endpoint
- returns `404 Not Found` when `METRICS_ENABLED=false`
- current Phase 10 metrics include:
  - HTTP request totals and latencies
  - task command counters
  - fetch result counters
  - parse result counters
  - verify result counters
  - report generation counters

## Research task endpoints

### `POST /api/v1/research/tasks`

Purpose: create a research task in `PLANNED` status and emit `task.created`.

Request:

```json
{
  "query": "近30天 NVIDIA 在开源模型生态上的关键发布与影响",
  "constraints": {
    "domains_allow": ["nvidia.com", "github.com"],
    "language": "zh-CN"
  }
}
```

Response `201 Created`:

```json
{
  "task_id": "uuid",
  "status": "PLANNED",
  "revision_no": 1,
  "updated_at": "2026-04-22T12:00:00Z"
}
```

### `GET /api/v1/research/tasks/{task_id}`

Purpose: return task metadata, current status, and minimal progress.

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "query": "近30天 NVIDIA 在开源模型生态上的关键发布与影响",
  "status": "PLANNED",
  "constraints": {
    "domains_allow": ["nvidia.com", "github.com"],
    "language": "zh-CN"
  },
  "revision_no": 1,
  "created_at": "2026-04-22T12:00:00Z",
  "updated_at": "2026-04-22T12:00:00Z",
  "started_at": null,
  "ended_at": null,
  "progress": {
    "current_state": "PLANNED",
    "events_total": 1,
    "latest_event_at": "2026-04-22T12:00:00Z",
    "observability": null
  }
}
```

When a task has run through the synchronous pipeline, `progress.observability` is derived from task events and may include:

- `search_result_count`
- `selected_sources`
- `fetch_succeeded`
- `fetch_failed`
- `failed_sources` with URL, HTTP status, error code, and error reason when available
- `parse_decisions` with per-snapshot parsing outcome details when parsing has run or failed
- non-blocking `warnings`, such as fewer than two successful fetched sources

### `GET /api/v1/research/tasks/{task_id}/events`

Purpose: return the ordered task event stream.

Query parameters:

- `after_sequence_no`: optional exclusive lower bound for polling
- `limit`: optional page size cap, `1..500`

Read contract:

- events are always returned in ascending `sequence_no`
- when query parameters are omitted, the endpoint remains backward compatible and returns the full ordered stream
- `created_at` remains informational; clients should use `sequence_no` for stable per-task ordering

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "events": [
    {
      "event_id": "uuid",
      "run_id": null,
      "event_type": "task.created",
      "sequence_no": 1,
      "payload": {
        "event_version": 1,
        "source": "api",
        "from_status": null,
        "to_status": "PLANNED",
        "changes": {
          "query": "近30天 NVIDIA 在开源模型生态上的关键发布与影响",
          "revision_no": 1,
          "constraints": {
            "domains_allow": ["nvidia.com", "github.com"],
            "language": "zh-CN"
          }
        }
      },
      "created_at": "2026-04-22T12:00:00Z"
    }
  ]
}
```

### `POST /api/v1/research/tasks/{task_id}/pause`

Purpose: perform the minimal Phase 2 transition `PLANNED -> PAUSED` and emit `task.paused`.

### `POST /api/v1/research/tasks/{task_id}/resume`

Purpose: perform the minimal Phase 2 transition `PAUSED -> PLANNED` and emit `task.resumed`.

Semantics: `resume` currently means “return to the executable-candidate status” only. It does not enqueue work, start a worker, or imply `QUEUED` or `RUNNING`.

### `POST /api/v1/research/tasks/{task_id}/cancel`

Purpose: perform the minimal Phase 2 transition `PLANNED|PAUSED -> CANCELLED` and emit `task.cancelled`.

### `POST /api/v1/research/tasks/{task_id}/revise`

Purpose: update the persisted task query and or constraints, increment `revision_no`, return the task to `PLANNED`, and emit `task.revised`.

Revision semantics:

- `query`, if present, replaces the stored query
- `constraints`, if present, is a shallow top-level merge into the stored `constraints`
- constraint deletion and deep merge are not supported in the current phase

Request:

```json
{
  "query": "聚焦 NVIDIA 与开源推理栈",
  "constraints": {
    "max_rounds": 2
  }
}
```

Command responses use the same shape:

```json
{
  "task_id": "uuid",
  "status": "PLANNED",
  "revision_no": 2,
  "updated_at": "2026-04-22T12:05:00Z"
}
```

## Phase 2 task transition rules

- `pause`: allowed only from `PLANNED`
- `resume`: allowed only from `PAUSED`
- `cancel`: allowed from `PLANNED` and `PAUSED`
- `revise`: allowed from `PLANNED` and `PAUSED`, and always results in `PLANNED`
- invalid transitions return `409 Conflict`
- unknown task ids return `404 Not Found`

## Search discovery endpoints

### `POST /api/v1/research/tasks/{task_id}/searches`

Purpose: execute the minimal synchronous Phase 3 search discovery flow for the current task query, persist `search_query` rows for each executed expanded query, canonicalize and filter result URLs, then persist deduped `candidate_url` rows.

Semantics:

- allowed only when the task status is `PLANNED`
- does not change `research_task.status`
- does not emit new `task_event` types in Phase 3
- creates `research_run` round `1` on the first search for a task
- reuses the latest run while `revision_no` is unchanged
- creates a new `research_run` round after `revise` increments `revision_no`

Response `201 Created`:

```json
{
  "task_id": "uuid",
  "run_id": "uuid",
  "round_no": 1,
  "revision_no": 1,
  "search_queries": [
    {
      "search_query_id": "uuid",
      "query_text": "近30天 NVIDIA 在开源模型生态上的关键发布与影响",
      "provider": "searxng",
      "source_engines": ["bing", "google"],
      "round_no": 1,
      "issued_at": "2026-04-23T12:10:00Z",
      "candidates_added": 2,
      "duplicates_skipped": 1,
      "filtered_out": 1
    }
  ],
  "candidate_urls_added": 2,
  "duplicates_skipped": 1,
  "filtered_out": 1
}
```

### `GET /api/v1/research/tasks/{task_id}/search-queries`

Purpose: return the persisted search query ledger for a task.

Read contract:

- ordered by `round_no`, then ascending `issued_at`
- each record includes provider identity, discovered source engines, `result_count`, and the current minimum raw metadata contract

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "search_queries": [
    {
      "search_query_id": "uuid",
      "query_text": "site:nvidia.com 近30天 NVIDIA 在开源模型生态上的关键发布与影响",
      "provider": "searxng",
      "source_engines": ["google"],
      "round_no": 1,
      "issued_at": "2026-04-23T12:10:01Z",
      "result_count": 3,
      "metadata": {
        "task_revision_no": 1,
        "expansion_kind": "site",
        "expansion_metadata": {
          "domain": "nvidia.com"
        },
        "source_engines": ["google"],
        "response_metadata": {
          "request_params": {
            "q": "site:nvidia.com 近30天 NVIDIA 在开源模型生态上的关键发布与影响",
            "format": "json"
          }
        },
        "result_count": 3
      }
    }
  ]
}
```

### `GET /api/v1/research/tasks/{task_id}/candidate-urls`

Purpose: return the currently persisted candidate URL ledger for a task.

Query parameters:

- `domain`: optional exact domain filter after canonicalization
- `selected`: optional boolean filter
- `limit`: optional cap, `1..500`

Read contract:

- results are ordered by search discovery order, then provider rank within each persisted query
- URLs are canonicalized before task-scoped dedupe
- `selected` remains `false` by default in Phase 5 because no fetch-selection policy exists yet

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "candidate_urls": [
    {
      "candidate_url_id": "uuid",
      "search_query_id": "uuid",
      "original_url": "https://www.nvidia.com/en-us/blog/example/?utm_source=x&id=1",
      "canonical_url": "https://www.nvidia.com/en-us/blog/example/?id=1",
      "domain": "www.nvidia.com",
      "title": "Example source",
      "rank": 1,
      "selected": false,
      "metadata": {
        "provider": "searxng",
        "source_engine": "google",
        "snippet": "Example snippet",
        "result_metadata": {
          "category": "general",
          "published_date": "2026-04-20",
          "score": 12.5
        },
        "task_revision_no": 1,
        "expansion_kind": "base",
        "expansion_metadata": {},
        "query_text": "近30天 NVIDIA 在开源模型生态上的关键发布与影响"
      }
    }
  ]
}
```

## Phase 3 search discovery rules

- query expansion currently emits one base query plus `site:<domain>` expansions derived from `constraints.domains_allow`
- `candidate_url` intake canonicalizes URLs before dedupe
- allow or deny filtering uses canonicalized domains; deny rules override allow rules
- task-scoped dedupe is applied in the service layer before insert, so the same canonical URL is persisted at most once per task in the current implementation
- `search_query.provider` stores the provider id, currently `searxng`
- per-result `source_engine` and provider metadata are stored in `candidate_url.metadata_json`
- `search_query.raw_response_json` currently stores `task_revision_no`, expansion metadata, discovered source engines, provider response metadata, and `result_count`
- the SearXNG provider validates endpoint responses before they enter the ledger:
  - HTML responses are rejected as `searxng_html_response`
  - HTTP 403 is rejected as `searxng_http_forbidden`
  - invalid JSON is rejected as `searxng_invalid_json`
  - empty results with `unresponsive_engines` are rejected as `searxng_empty_results_with_unresponsive_engines`
- SearXNG diagnostics are logged with `SEARCH_PROVIDER`, `SEARXNG_BASE_URL`, response status, content type, body preview, and `unresponsive_engines`
- no fetch jobs, fetch attempts, crawler calls, parser calls, OpenSearch writes, claim drafting, verification, or report generation are triggered by `POST /searches`
- paused or cancelled tasks return `409 Conflict` from `POST /searches`

## Reserved runtime statuses

The schema and code now reserve these later-phase runtime-facing statuses:

- `QUEUED`
- `RUNNING`
- `FAILED`
- `COMPLETED`
- `NEEDS_REVISION`

They are not user-writable through the current Phase 6 API.

## Acquisition endpoints

### `POST /api/v1/research/tasks/{task_id}/fetches`

Purpose: execute the minimal synchronous Phase 4 acquisition flow for existing `candidate_url` rows, creating `fetch_job` and `fetch_attempt`, executing a policy-guarded HTTP fetch, and persisting a `content_snapshot` when raw bytes are successfully captured and stored.

Request:

```json
{
  "candidate_url_ids": ["uuid"],
  "limit": 5
}
```

Request semantics:

- request body is optional
- when `candidate_url_ids` is omitted, the service scans persisted task candidates in discovery order and creates new `HTTP` fetch jobs until `limit` or the configured server-side cap is reached
- when `candidate_url_ids` is provided, ids must belong to the task; duplicate ids in the request are ignored after first occurrence
- `limit` is optional and bounded to `1..50`, then capped again by the server-side `ACQUISITION_MAX_CANDIDATES_PER_REQUEST`

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "created": 1,
  "skipped_existing": 0,
  "succeeded": 1,
  "failed": 0,
  "entries": [
    {
      "candidate_url_id": "uuid",
      "canonical_url": "https://example.com/",
      "fetch_job_id": "uuid",
      "fetch_attempt_id": "uuid",
      "snapshot_id": "uuid",
      "status": "SUCCEEDED",
      "http_status": 200,
      "error_code": null,
      "error_reason": null,
      "skipped_existing": false
    }
  ]
}
```

Command contract:

- allowed only when the task status is `PLANNED`
- creates at most one `fetch_job` per `(candidate_url_id, mode)` for `mode = HTTP`
- existing `HTTP` jobs are returned as `skipped_existing=true`; this is the current idempotency boundary
- creates `fetch_attempt.attempt_no = 1` for each new fetch job in Phase 4
- may return `status = FAILED` with an `error_code` even when `http_status` is present, such as for non-2xx responses or storage failures
- does not emit new `task_event` rows and does not change `research_task.status`

### `GET /api/v1/research/tasks/{task_id}/fetch-jobs`

Purpose: return persisted fetch-job ledger rows for a task.

Query parameters:

- `status`: optional exact fetch-job status filter
- `limit`: optional cap, `1..500`

Read contract:

- ordered by `scheduled_at`, then ascending `fetch_job_id`
- includes the latest known attempt summary and the current snapshot id, if any

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "fetch_jobs": [
    {
      "fetch_job_id": "uuid",
      "candidate_url_id": "uuid",
      "canonical_url": "https://example.com/",
      "mode": "HTTP",
      "status": "SUCCEEDED",
      "scheduled_at": "2026-04-23T13:00:00Z",
      "latest_attempt_id": "uuid",
      "latest_attempt_no": 1,
      "latest_http_status": 200,
      "latest_error_code": null,
      "latest_error_reason": null,
      "snapshot_id": "uuid"
    }
  ]
}
```

### `GET /api/v1/research/tasks/{task_id}/fetch-attempts`

Purpose: return persisted fetch-attempt ledger rows for a task.

Query parameters:

- `fetch_job_id`: optional exact job filter
- `limit`: optional cap, `1..500`

Read contract:

- ordered by `started_at`, then ascending `fetch_attempt_id`
- `trace` carries the minimum acquisition trace for the attempt, including final URL, redirect chain, resolved IPs, byte counts, and explicit policy or storage failure details when applicable

### `GET /api/v1/research/tasks/{task_id}/content-snapshots`

Purpose: return persisted content-snapshot ledger rows for a task.

Query parameters:

- `limit`: optional cap, `1..500`

Read contract:

- ordered by `fetched_at`, then ascending `snapshot_id`
- each row returns only object reference and basic metadata; it does not stream raw content

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "content_snapshots": [
    {
      "snapshot_id": "uuid",
      "fetch_attempt_id": "uuid",
      "storage_bucket": "snapshots",
      "storage_key": "research-task/<task_id>/candidate-url/<candidate_url_id>/fetch-attempt/<fetch_attempt_id>/response.bin",
      "content_hash": "sha256:0123456789abcdef",
      "mime_type": "text/html",
      "bytes": 1256,
      "extracted_title": null,
      "fetched_at": "2026-04-23T13:00:01Z"
    }
  ]
}
```

## Phase 4 acquisition rules

| Boundary | Current rule |
| --- | --- |
| Allowed schemes | `http`, `https` |
| Blocked hostnames | `localhost`, `metadata`, `metadata.google.internal` |
| Blocked resolved targets | loopback, private, link-local, and any other non-global IP when all DNS answers are non-global |
| Mixed DNS answers | allowed when at least one resolved IP is global; trace includes `allowed_ips`, `blocked_ips`, and `decision_reason` |
| Timeout | bounded by `ACQUISITION_TIMEOUT_SECONDS` |
| Redirects | bounded by `ACQUISITION_MAX_REDIRECTS` |
| Max response body | bounded by `ACQUISITION_MAX_RESPONSE_BYTES` |
| Snapshot backend | filesystem-backed object store interface in current phase |
| Non-2xx behavior | persisted as failed attempts with `error_code = "http_error_status"` |
| Storage write failure | persisted as failed attempts with `error_code = "storage_write_failed"` |

## Parsing endpoints

### `POST /api/v1/research/tasks/{task_id}/parse`

Purpose: execute the minimal synchronous Phase 5 parsing flow for existing `content_snapshot` rows, read stored snapshot bytes, extract minimal text from supported MIME types, persist or update one provenance-linked `source_document`, and persist stable `source_chunk` rows.

Request:

```json
{
  "content_snapshot_ids": ["uuid"],
  "limit": 5
}
```

Request semantics:

- request body is optional
- when `content_snapshot_ids` is omitted, the service scans persisted task snapshots in ascending `fetched_at` order
- when `content_snapshot_ids` is provided, ids must belong to the task; duplicate ids in the request are ignored after first occurrence
- `limit` is optional and bounded to `1..50`, then capped again by the current server-side parse cap
- only already-fetched successful snapshots are eligible for parsing; other snapshots are skipped with an explicit reason

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "created": 1,
  "updated": 0,
  "skipped_existing": 0,
  "skipped_unsupported": 0,
  "failed": 0,
  "entries": [
    {
      "content_snapshot_id": "uuid",
      "source_document_id": "uuid",
      "canonical_url": "https://example.com/",
      "mime_type": "text/html",
      "content_type": "text/html",
      "storage_bucket": "snapshots",
      "storage_key": "research-task/uuid/candidate-url/uuid/fetch-attempt/uuid/response.bin",
      "snapshot_bytes": 286,
      "body_length": 286,
      "chunks_created": 1,
      "status": "CREATED",
      "reason": null,
      "decision": "parsed",
      "parser_error": null,
      "updated_existing": false
    }
  ]
}
```

Command contract:

- allowed only when the task status is `PLANNED`
- currently supports only `text/html` and `text/plain`
- parse entry `reason` uses this stable enum when present:
  - `fetch_not_succeeded`
  - `already_parsed`
  - `snapshot_object_missing`
  - `unsupported_mime_type`
  - `empty_extracted_text`
  - `parse_error`
- parse entry `decision` is the operator-facing outcome and may be:
  - `parsed`
  - `already_parsed`
  - `fetch_not_succeeded`
  - `skipped_empty`
  - `skipped_unsupported_mime`
  - `missing_blob`
  - `parse_error`
- unsupported MIME types are skipped with `reason = "unsupported_mime_type"`
- if the snapshot object is missing from storage, the entry is returned as `FAILED` with `reason = "snapshot_object_missing"`
- empty extracted text is skipped with `decision = "skipped_empty"` and includes `body_length`
- parser exceptions are returned as `FAILED` with `decision = "parse_error"` and `parser_error`
- if a `source_document` already points at the same `content_snapshot`, the entry is skipped with `reason = "already_parsed"`
- if a `source_document` already exists for the same `(task_id, canonical_url)` but points at an older or null snapshot, the current minimum behavior is to update that row, move its `content_snapshot_id`, and rebuild its chunks
- does not emit new `task_event` rows and does not change `research_task.status`

### `GET /api/v1/research/tasks/{task_id}/source-documents`

Purpose: return persisted `source_document` rows for a task.

Query parameters:

- `limit`: optional cap, `1..500`

Read contract:

- ordered by `fetched_at`, then ascending `source_document_id`
- each row includes `content_snapshot_id` so parsed output remains traceable to the exact snapshot used
- current rows are current-state source records, not a parse-history version chain

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "source_documents": [
    {
      "source_document_id": "uuid",
      "content_snapshot_id": "uuid",
      "canonical_url": "https://example.com/",
      "domain": "example.com",
      "title": "Example Domain",
      "source_type": "web_page",
      "published_at": null,
      "fetched_at": "2026-04-23T13:00:01Z"
    }
  ]
}
```

### `GET /api/v1/research/tasks/{task_id}/sources`

Purpose: return the web-workspace source summary for a task. This is a thin read-only alias over the current `source_document` ledger so the operator UI can treat a newly created `PLANNED` task as a valid empty source workspace.

Query parameters:

- `limit`: optional cap, `1..500`

Read contract:

- no search, fetch, parse, index, claim, worker, or report side effects are triggered
- unknown task ids return `404 Not Found`
- existing tasks with no parsed source documents return `200 OK` with an empty `sources` array
- source item fields currently match the `source_document` read model

Response `200 OK` for an existing task with no parsed sources:

```json
{
  "task_id": "uuid",
  "sources": []
}
```

### `GET /api/v1/research/tasks/{task_id}/source-chunks`

Purpose: return persisted `source_chunk` rows for a task.

Query parameters:

- `source_document_id`: optional exact document filter
- `limit`: optional cap, `1..500`

Read contract:

- ordered by document `fetched_at`, then ascending `source_document_id`, then ascending `chunk_no`
- each row includes the parent `content_snapshot_id` and minimum chunk metadata

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "source_chunks": [
    {
      "source_chunk_id": "uuid",
      "source_document_id": "uuid",
      "content_snapshot_id": "uuid",
      "chunk_no": 0,
      "token_count": 83,
      "text": "Example body text",
      "metadata": {
        "strategy": "paragraph_window_v1",
        "char_count": 332,
        "paragraph_count": 2,
        "approx_token_count": 83,
        "content_snapshot_id": "uuid",
        "mime_type": "text/html",
        "extractor": "html_text_v1"
      }
    }
  ]
}
```

## Phase 5 parsing rules

- parsing reads raw bytes from the configured snapshot object store; unsupported snapshot backends now fail during app startup
- only snapshots from successful fetches are eligible for parsing
- supported extractors are currently:
  - `html_text_v1` for `text/html`
  - `plain_text_v1` for `text/plain`
- HTML extraction keeps `<title>` for `source_document.title` but excludes it from body chunks
- plain-text title derivation currently uses the first non-empty line
- chunking currently uses the stable `paragraph_window_v1` strategy:
  - normalize text and paragraph breaks
  - accumulate paragraphs into chunks up to roughly `1200` characters
  - split a single oversized paragraph into fixed windows
  - no overlap in the current phase
- `source_chunk.token_count` is currently a stable approximation derived from character length
- Tika, PDF or Office parsing, claim drafting, verification, and report generation remain out of scope for the parsing slice

## Indexing endpoints

### `POST /api/v1/research/tasks/{task_id}/index`

Purpose: execute the minimal synchronous Phase 6 indexing flow for persisted `source_chunk` rows and upsert them into the configured chunk-index backend.

Request:

```json
{
  "source_chunk_ids": ["uuid"],
  "limit": 10
}
```

Request semantics:

- request body is optional
- when `source_chunk_ids` is omitted, the service scans persisted task chunks in stable document and chunk order
- when `source_chunk_ids` is provided, ids must belong to the task; duplicate ids in the request are ignored after first occurrence
- `limit` is optional and bounded to `1..100`, then capped again by the current server-side indexing cap

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "indexed_count": 1,
  "indexed_chunks": [
    {
      "task_id": "uuid",
      "source_document_id": "uuid",
      "source_chunk_id": "uuid",
      "canonical_url": "https://example.com/",
      "domain": "example.com",
      "chunk_no": 0,
      "text": "Example body text",
      "metadata": {
        "strategy": "paragraph_window_v1"
      },
      "score": null
    }
  ]
}
```

Command contract:

- allowed only when the task status is `PLANNED`
- index writes are deterministic upserts keyed by `source_chunk_id`
- does not change `research_task.status` and does not emit new `task_event` rows
- does not create claim, verification, or report records

### `GET /api/v1/research/tasks/{task_id}/indexed-chunks`

Purpose: return thin debug visibility into currently indexed chunk documents for a task.

Query parameters:

- `offset`: optional page offset, `>= 0`
- `limit`: optional page size cap, `1..100`, then capped again by the server-side retrieval limit

Read contract:

- ordered by ascending `source_document_id`, then ascending `chunk_no`, then ascending `source_chunk_id`
- returns whatever is currently present in the index backend for that task
- if the index does not exist yet, returns an empty page instead of an error

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "total": 1,
  "offset": 0,
  "limit": 20,
  "indexed_chunks": [
    {
      "task_id": "uuid",
      "source_document_id": "uuid",
      "source_chunk_id": "uuid",
      "canonical_url": "https://example.com/",
      "domain": "example.com",
      "chunk_no": 0,
      "text": "Example body text",
      "metadata": {
        "strategy": "paragraph_window_v1"
      },
      "score": null
    }
  ]
}
```

### `GET /api/v1/research/tasks/{task_id}/retrieve`

Purpose: run the minimal Phase 6 task-scoped retrieval query over indexed chunks.

Query parameters:

- `query`: required, non-blank retrieval text
- `offset`: optional page offset, `>= 0`
- `limit`: optional page size cap, `1..100`, then capped again by the server-side retrieval limit

Read contract:

- retrieval is restricted to one `task_id`
- current implementation uses a simple text `match` over indexed chunk `text`
- results are ordered by descending score, then ascending `source_document_id`, `chunk_no`, and `source_chunk_id`

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "query": "beta",
  "total": 1,
  "offset": 0,
  "limit": 20,
  "hits": [
    {
      "task_id": "uuid",
      "source_document_id": "uuid",
      "source_chunk_id": "uuid",
      "canonical_url": "https://example.com/",
      "domain": "example.com",
      "chunk_no": 0,
      "text": "Alpha beta gamma",
      "metadata": {
        "strategy": "paragraph_window_v1"
      },
      "score": 1.0
    }
  ]
}
```

## Phase 6 indexing and retrieval rules

- startup validates configured snapshot and index backends, but does not require live OpenSearch reachability
- the current index backend is an OpenSearch REST implementation behind a minimal abstraction
- index documents are traceable to relational ledger rows through `source_chunk_id` and `source_document_id`
- retrieval is deliberately simple and explainable:
  - exact task filter
  - text `match`
  - no embeddings
  - no hybrid search
  - no reranking beyond backend score and stable tie-breakers
- report generation remains out of scope for the indexing slice

## Claim drafting and verification endpoints

### `POST /api/v1/research/tasks/{task_id}/claims/draft`

Purpose: execute the current minimal claim-drafting flow for a task, selecting chunks from retrieval or explicit `source_chunk` ids, drafting support-only claims, and binding each claim to one validated `citation_span` plus one `claim_evidence` row.

Request:

```json
{
  "query": "illustrative examples",
  "source_chunk_ids": ["uuid"],
  "limit": 5
}
```

Request semantics:

- at least one of `query` or `source_chunk_ids` is required
- when `source_chunk_ids` is omitted, the service uses task-scoped retrieval over the indexed chunks
- when `source_chunk_ids` is provided, ids must belong to the task; duplicate ids in the request are ignored after first occurrence
- when `query` is omitted but `source_chunk_ids` is provided, the service falls back to the persisted task query for sentence selection and confidence scoring
- `limit` is optional and bounded to `1..100`, then capped again by the server-side claim-drafting candidate cap

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "effective_query": "illustrative examples",
  "created_claims": 1,
  "reused_claims": 0,
  "created_citation_spans": 1,
  "reused_citation_spans": 0,
  "created_claim_evidence": 1,
  "reused_claim_evidence": 0,
  "claims": [
    {
      "claim_id": "uuid",
      "citation_span_id": "uuid",
      "claim_evidence_id": "uuid",
      "source_chunk_id": "uuid",
      "source_document_id": "uuid",
      "statement": "This domain is for use in illustrative examples in documents and test content.",
      "claim_type": "fact",
      "confidence": 0.73,
      "verification_status": "draft",
      "relation_type": "support",
      "evidence_score": 0.73,
      "start_offset": 16,
      "end_offset": 94,
      "excerpt": "This domain is for use in illustrative examples in documents and test content.",
      "reused_claim": false,
      "reused_citation_span": false,
      "reused_claim_evidence": false,
      "retrieval_score": 1.0
    }
  ]
}
```

Command contract:

- allowed only when the task status is `PLANNED`
- drafting remains support-only; it does not create contradiction or mixed-evidence judgments
- drafting sets `verification_status = "draft"` only; no verifier semantics are implied
- each created or reused citation span is validated against the exact `source_chunk.text` slice before use
- repeated calls are guarded by exact-statement claim reuse and existing citation or claim-evidence uniqueness boundaries
- does not emit new `task_event` rows and does not change `research_task.status`

### `GET /api/v1/research/tasks/{task_id}/claims`

Purpose: return persisted task claims for a task, including the current minimal verification bundle summary.

Query parameters:

- `verification_status`: optional exact filter
- `limit`: optional cap, `1..500`

Read contract:

- ordered by ascending `claim_id`
- each item includes:
  - `statement`
  - `claim_type`
  - `confidence`
  - `verification_status`
  - `support_evidence_count`
  - `contradict_evidence_count`
  - `rationale`
  - `notes`

### `GET /api/v1/research/tasks/{task_id}/claim-evidence`

Purpose: return persisted claim-evidence bindings for a task.

Query parameters:

- `claim_id`: optional exact claim filter
- `relation_type`: optional exact relation filter
- `limit`: optional cap, `1..500`

Read contract:

- ordered by ascending `claim_id`, then ascending `claim_evidence_id`
- each item includes the exact `citation_span` offsets and excerpt so the evidence chain is reconstructible without additional joins
- `relation_type` currently uses the minimum stable Phase 8 set:
  - `support`
  - `contradict`

### `POST /api/v1/research/tasks/{task_id}/claims/verify`

Purpose: execute the minimal synchronous Phase 8 verification flow for existing task claims, reusing current retrieval to scan task-scoped `source_chunk` candidates, adding any new `support` or `contradict` evidence links, and updating each processed claim to `supported`, `mixed`, or `unsupported`.

Request:

```json
{
  "claim_ids": ["uuid"],
  "limit": 5
}
```

Request semantics:

- `claim_ids` is optional; when omitted, the service verifies up to `limit` claims currently in `verification_status = "draft"`
- when `claim_ids` is provided, ids must belong to the task; duplicate ids in the request are ignored after first occurrence
- `limit` is optional and bounded to `1..100`, then capped again by the server-side verification claim cap
- retrieval query is derived from each persisted `claim.statement`; the current phase does not accept custom verifier prompts or external evidence payloads

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "verified_claims": 1,
  "created_citation_spans": 1,
  "reused_citation_spans": 1,
  "created_claim_evidence": 1,
  "reused_claim_evidence": 1,
  "claims": [
    {
      "claim_id": "uuid",
      "statement": "This domain is for use in illustrative examples in documents and test content.",
      "claim_type": "fact",
      "confidence": 0.73,
      "verification_status": "mixed",
      "support_evidence_count": 1,
      "contradict_evidence_count": 1,
      "rationale": "Found 1 support evidence and 1 contradict evidence.",
      "notes": {
        "verification": {
          "method": "retrieval_conflict_scan_v1",
          "verification_query": "This domain is for use in illustrative examples in documents and test content.",
          "support_evidence_count": 1,
          "contradict_evidence_count": 1,
          "rationale": "Found 1 support evidence and 1 contradict evidence."
        }
      }
    }
  ]
}
```

Command contract:

- allowed only when the task status is `PLANNED`
- verification builds on the current retrieval, `citation_span`, and `claim_evidence` seams
- verification adds only these evidence relations:
  - `support`
  - `contradict`
- verification updates `claim.verification_status` only within this minimum stable set:
  - `draft`
  - `supported`
  - `mixed`
  - `unsupported`
- exact citation-span validation still applies before create or reuse:
  - `start_offset < end_offset`
  - `excerpt == source_chunk.text[start_offset:end_offset]`
- repeated verification calls are guarded by exact citation-span reuse plus the existing `claim_evidence(claim_id, citation_span_id, relation_type)` uniqueness boundary
- does not emit new `task_event` rows and does not change `research_task.status`

## Phase 8 claim drafting and verification rules

- current draft claims use the stable singleton `claim_type = "fact"`
- current draft requests still create `verification_status = "draft"` only
- verification then resolves claims into the minimum stable set:
  - `draft`
  - `supported`
  - `mixed`
  - `unsupported`
- current claim evidence uses this minimum stable set:
  - `support`
  - `contradict`
- citation spans are validated before create or reuse:
  - `start_offset < end_offset`
  - `excerpt` must exactly equal the corresponding `source_chunk.text` slice
- current confidence is a minimal heuristic derived from query overlap, statement length, and retrieval score when present
- claim drafting now filters weak deterministic candidates before persistence:
  - statements must be complete sentence-like text with minimum length and token content
  - one-word or short fragments such as `C` or `Data` are skipped
  - title/question-like statements such as `What is OpenAI?` are skipped, especially when they duplicate the task query
  - duplicate statements are deduped using a case- and punctuation-normalized identity
- verification skips citation excerpts that fail the same minimum claimable-excerpt rules, so short fragments are not added as support evidence
- verification is deterministic and explainable:
  - retrieve task-scoped chunks by `claim.statement`
  - classify the best sentence-like span as `support`, `contradict`, or no match
  - persist or reuse the exact `citation_span`
  - persist or reuse the relation-specific `claim_evidence`
  - aggregate support and contradict counts into `claim.notes["verification"]`

## Report endpoints

### `POST /api/v1/research/tasks/{task_id}/report`

Purpose: execute the minimal synchronous Phase 9 report-synthesis flow for a task, rendering one Markdown artifact strictly from the persisted task, claim, citation, evidence, and verification ledger, then storing it through the existing object-store abstraction and recording one `report_artifact` row when the rendered bytes differ from the latest stored Markdown artifact.

Request semantics:

- no request body is required in the current phase
- report generation does not run new retrieval, verification, or claim-drafting logic
- report generation may be invoked for any existing task because it synthesizes from persisted ledger state only

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "report_artifact_id": "uuid",
  "version": 1,
  "format": "markdown",
  "title": "Research Report: What is the current verified position?",
  "storage_bucket": "reports",
  "storage_key": "uuid/v1/report.md",
  "created_at": "2026-04-24T10:30:00Z",
  "supported_claims": 1,
  "mixed_claims": 1,
  "unsupported_claims": 1,
  "draft_claims": 0,
  "reused_existing": false,
  "markdown": "# Research Report: What is the current verified position?\n..."
}
```

Command contract:

- report synthesis is evidence-first and built only from existing:
  - `research_task`
  - `claim`
  - `citation_span`
  - `claim_evidence`
  - `verification_status`
- `supported` claims may appear as settled conclusions
- `mixed`, `unsupported`, and `draft` claims must remain explicitly labeled in the report body
- repeated calls reuse the latest artifact when the newly rendered Markdown bytes are identical
- repeated calls create a new Markdown artifact version only when the rendered content changes
- does not emit new `task_event` rows and does not change `research_task.status`

### `GET /api/v1/research/tasks/{task_id}/report`

Purpose: return the latest persisted Markdown report artifact for a task.

Read contract:

- returns the latest `report_artifact` with `format = "markdown"`
- returns `404 Not Found` if no Markdown report artifact exists for the task
- returns `500 Internal Server Error` if the ledger row exists but the stored artifact object is missing
- returns `500 Internal Server Error` if the stored artifact bytes fail `content_hash` verification
- the returned Markdown is the stored artifact content, not a live re-render
- the response contains artifact metadata plus the stored Markdown body only; synthesis count fields are returned by `POST /report`, not by `GET /report`

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "report_artifact_id": "uuid",
  "version": 1,
  "format": "markdown",
  "title": "Research Report: What is the current verified position?",
  "storage_bucket": "reports",
  "storage_key": "uuid/v1/report.md",
  "created_at": "2026-04-24T10:30:00Z",
  "markdown": "# Research Report: What is the current verified position?\n..."
}
```

## Phase 9 plus Phase 10 report synthesis rules

- current report output format is `markdown` only
- current report generation is deterministic and explainable:
  - read persisted claims and evidence
  - read persisted verification status and rationale
  - render a Markdown report with fixed section structure
  - store the artifact through the existing object-store abstraction
  - persist a `report_artifact(task_id, version, format="markdown")` ledger row
- the current Markdown report includes:
  - title
  - research question
  - executive summary
  - method and source scope
  - key conclusions
  - conclusion details and evidence
  - conflicts / uncertainty
  - unresolved questions
  - appendix: source list
  - appendix: claim to citation spans mapping
- current report synthesis never invents unsupported conclusions:
  - only `supported` claims appear as settled conclusions
  - `mixed` and `unsupported` claims are rendered only inside uncertainty-aware sections with explicit status labels
- report synthesis filters historical weak ledger material before rendering:
  - non-claimable title/question/fragment statements are skipped
  - citation excerpts below the minimum claimable threshold are not rendered as support evidence
  - claims marked `supported` or `mixed` without remaining support evidence are downgraded to `unsupported` in the rendered report
- current storage uses the existing object-store abstraction with the configured report bucket
- Phase 10 now persists additional internal report-artifact provenance:
  - `content_hash`
  - `manifest_json`
- `GET /report` now verifies stored Markdown bytes against `content_hash` when that hash is present
  - hash mismatch is treated as `500 Internal Server Error`
- report endpoint response bodies are unchanged in Phase 10; the new provenance fields are internal ledger hardening, not a new product response contract
- the product run endpoint now reports the current runtime stage through `research_task.status` and `progress.current_state`; after a failed stage the task remains inspectable in `FAILED`

## DeepSearch run endpoint

### `POST /api/v1/research/tasks/{task_id}/run`

Purpose: run one existing `research_task` through the current synchronous DeepSearch loop from the frontend or host-local API tooling.

Execution contract:

- starts only from `research_task.status = "PLANNED"`
- does not use a worker, queue, Celery, LangGraph runner, or LLM
- reuses the existing service-layer seams:
  - search discovery
  - HTTP acquisition
  - parsing and chunking
  - indexing and retrieval
  - claim drafting
  - claim verification
  - Markdown report generation
- transitions `research_task.status` through:
  - `RUNNING`
  - `SEARCHING`
  - `ACQUIRING`
  - `PARSING`
  - `INDEXING`
  - `DRAFTING_CLAIMS`
  - `VERIFYING`
  - `REPORTING`
  - `COMPLETED`
- on stage failure, transitions the task to `FAILED`, emits `pipeline.failed`, and returns a structured failure object instead of an unstructured 500
- emits `pipeline.started`, `pipeline.stage_started`, `pipeline.stage_completed`, `pipeline.failed`, and `pipeline.completed` task events
- `pipeline.stage_completed` and `pipeline.failed` payloads include operator observability details for search, acquisition, and parsing:
  - search result count and selected candidate source summaries
  - fetch success/failure counts
  - failed fetch URL summaries with HTTP status, error code, and error reason
  - parse decisions with snapshot id, canonical URL, MIME type, storage location, body length, decision, and parser error when present
  - warnings when fewer than two sources fetch successfully; this does not block completion when at least one source succeeds

Response `200 OK` on completion:

```json
{
  "task_id": "uuid",
  "status": "COMPLETED",
  "completed": true,
  "running_mode": "real-search+opensearch+no-LLM",
  "stages_completed": [
    "SEARCHING",
    "ACQUIRING",
    "PARSING",
    "INDEXING",
    "DRAFTING_CLAIMS",
    "VERIFYING",
    "REPORTING"
  ],
  "counts": {
    "search_queries": 1,
    "candidate_urls": 3,
    "fetch_attempts": 3,
    "content_snapshots": 2,
    "source_documents": 2,
    "source_chunks": 4,
    "indexed_chunks": 4,
    "claims": 4,
    "claim_evidence": 4,
    "report_artifacts": 1
  },
  "report_artifact_id": "uuid",
  "report_version": 1,
  "report_markdown_preview": "# Research Report: ...",
  "failure": null,
  "dependencies": {
    "search_provider": "searxng",
    "search_mode": "real-search",
    "index_backend": "opensearch",
    "index_mode": "opensearch",
    "llm_mode": "no-LLM",
    "uses_worker_or_queue": false
  }
}
```

Response `200 OK` on a handled stage failure:

```json
{
  "task_id": "uuid",
  "status": "FAILED",
  "completed": false,
  "running_mode": "real-search+opensearch+no-LLM",
  "stages_completed": [],
  "counts": {
    "search_queries": 1,
    "candidate_urls": 0,
    "fetch_attempts": 0,
    "content_snapshots": 0,
    "source_documents": 0,
    "source_chunks": 0,
    "indexed_chunks": 0,
    "claims": 0,
    "claim_evidence": 0,
    "report_artifacts": 0
  },
  "failure": {
    "failed_stage": "SEARCHING",
    "reason": "pipeline_precondition_failed",
    "exception": "DebugPipelinePreconditionError",
    "message": "search produced no candidate URLs",
    "next_action": "Check SEARCH_PROVIDER and SEARXNG_BASE_URL...",
    "counts": {
      "search_queries": 1,
      "candidate_urls": 0,
      "fetch_attempts": 0,
      "content_snapshots": 0,
      "source_documents": 0,
      "source_chunks": 0,
      "indexed_chunks": 0,
      "claims": 0,
      "claim_evidence": 0,
      "report_artifacts": 0
    }
  }
}
```

Development modes:

- `SEARCH_PROVIDER=smoke` uses a clearly marked smoke provider that returns `https://example.com/`; it is not real search
- `INDEX_BACKEND=local` uses a process-local deterministic index backend; it is not durable and is intended for development smoke only
- a smoke/local run reports `running_mode = "smoke-search+deterministic-local+no-LLM"`

## Development-only real pipeline debug endpoint

### `POST /api/v1/research/tasks/{task_id}/debug/run-real-pipeline`

Purpose: run the current real synchronous pipeline for one existing `research_task` during host-local development.

Availability:

- available only when `APP_ENV=development`
- returns `403 Forbidden` outside development
- intended for operator smoke validation, not production task execution

Execution contract:

- starts only from `research_task.status = "PLANNED"`
- does not use a worker, queue, Celery, LangGraph runner, or mock report
- serially reuses the same service-layer seams behind the existing endpoints:
  - search discovery
  - HTTP acquisition
  - parsing
  - OpenSearch indexing
  - claim drafting
- claim verification
- Markdown report generation
- moves `research_task.status` through the current runtime stages and then to `COMPLETED` or `FAILED`
- emits `task_event` rows for pipeline start, each stage start, each stage completion, failure, and completion
- on stage failure, stops immediately and returns a structured failure payload with stage, reason, exception type, message, next action, and current intermediate counts

Current stage sequence:

- `RUNNING`
- `SEARCHING`
- `ACQUIRING`
- `PARSING`
- `INDEXING`
- `DRAFTING_CLAIMS`
- `VERIFYING`
- `REPORTING`
- `COMPLETED`

External dependencies:

- `SEARXNG_BASE_URL`
- HTTP reachability for selected candidate URLs
- `SNAPSHOT_STORAGE_BACKEND` plus snapshot and report buckets or filesystem paths
- `INDEX_BACKEND=opensearch`
- `OPENSEARCH_BASE_URL`
- `OPENSEARCH_INDEX_NAME`

No LLM API is used by the current deterministic claim drafting and verification slices.

## Phase 10 infrastructure-hardening rules

- current object-store backends are:
  - `filesystem`
  - `minio`
- current MinIO startup validation is bucket-aware:
  - the configured snapshot and report buckets must exist before the app starts
- current OpenSearch startup validation is explicit and opt-in through `OPENSEARCH_VALIDATE_CONNECTIVITY_ON_STARTUP`
- current OpenSearch requests force `Accept-Encoding: identity` to avoid compressed-response read stalls observed against a real OpenSearch 2.19 tarball node
- current OpenSearch index mapping keeps the top-level document schema strict while allowing dynamic keys inside `metadata`
- current indexing and claim APIs now return `502 Bad Gateway` when the configured index backend fails during a real operation instead of surfacing an unstructured internal error
- current logs are JSON-formatted at the application layer
- current metrics are additive only; they do not imply worker orchestration or planner state

## Phase 11 deployment and smoke rules

- Phase 11 adds no new endpoint or response contract; it packages the existing API chain into host-local operational and smoke helpers
- the currently completed functional loop is:
  - `task -> search -> fetch -> parse -> index -> draft -> verify -> report`
- the recommended operational validation path is host-local Linux with real PostgreSQL, MinIO or filesystem storage, OpenSearch, and a running orchestrator process
- optional Docker or compose files may be used, but they are not the primary acceptance route
- `scripts/smoke_test.py` exercises this fixed API sequence:
  - `POST /api/v1/research/tasks`
  - `POST /api/v1/research/tasks/{task_id}/searches`
  - `POST /api/v1/research/tasks/{task_id}/fetches`
  - `POST /api/v1/research/tasks/{task_id}/parse`
  - `POST /api/v1/research/tasks/{task_id}/index`
  - `POST /api/v1/research/tasks/{task_id}/claims/draft`
  - `POST /api/v1/research/tasks/{task_id}/claims/verify`
  - `POST /api/v1/research/tasks/{task_id}/report`
- the smoke path is intentionally strict:
  - it fails if search discovery yields no `candidate_url`
  - it fails if no `content_snapshot`, `source_document`, or `source_chunk` is produced
  - it fails if indexing, claim drafting, verification, or report synthesis produces no persisted output
- Phase 11 also adds a deterministic repository-local `scripts/mock_searxng.py` helper so the API smoke can be reproduced without relying on a live external search backend

## Out of scope in Phase 11

- no worker-triggered execution starts after `resume` or `revise`
- no browser or Playwright fetching exists yet
- no Tika or attachment parsing exists yet
- no OpenClaw integration
- no HTML export or PDF export exists yet
- no multi-round planner or gap-analyzer logic exists yet
- no new verifier semantics beyond the current minimal support / contradict flow
- no complex retrieval or reranking optimization
