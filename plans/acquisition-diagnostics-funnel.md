# Acquisition funnel diagnostics and browser fetch seam

## 1. Objective

Improve observability of the search → fetch → parse → chunk funnel, refine static HTTP failure
classification, add static HTML quality heuristics, and document a reserved
`BrowserFetchBackend` integration point without implementing Playwright or MCP.

## 2. Why this exists

Phase 4 static HTTP acquisition loses many candidates before they become evidence. Operators need
ledger-backed funnel metrics and finer-grained `error_code` values to distinguish transport,
HTTP semantics, and weak HTML shells ahead of a future browser-rendered fallback.

## 3. Scope

### In scope

- DB-backed funnel diagnostics API and parse-decision aggregation from recent `task_event` rows
- httpx error classification, HTTP status refinements, redirect cap alias, static HTML gates
- Prometheus counter `deepresearch_fetch_failure_class_total`
- `BROWSER_FETCH_BACKEND` settings placeholder and `BrowserFetchBackend` protocol
- Unit tests for classification and HTML heuristics
- `docs/api.md` updates for acquisition rules and the new endpoint

### Out of scope

- Playwright or MCP implementations
- Schema migrations or changes to `candidate_url` → `claim_evidence` chain tables
- Automatic browser retries in `AcquisitionService`

## 4. Constraints

- Preserve ledger ordering: `candidate_url` → `fetch_attempt` → `content_snapshot` →
  `source_document` → `source_chunk` → downstream claim objects
- No new third-party runtime dependencies

## 5. Relevant files and systems

- `services/orchestrator/app/acquisition/http_client.py`
- `services/orchestrator/app/acquisition/failure_classification.py`
- `services/orchestrator/app/acquisition/html_quality.py`
- `services/orchestrator/app/acquisition/fetch_outcome.py`
- `services/orchestrator/app/acquisition/browser_backend.py`
- `services/orchestrator/app/services/acquisition.py`
- `services/orchestrator/app/services/acquisition_diagnostics.py`
- `services/orchestrator/app/api/routes/acquisition.py`
- `packages/observability/metrics.py`
- `docs/api.md`

## 6. Milestones

### M1 — Diagnostics API (done)

- `GET /api/v1/research/tasks/{task_id}/acquisition/funnel-metrics`
- Validation: hit endpoint against a task with ledger rows; JSON keys present

### M2 — Classification and HTML gate (done)

- Refined `error_code` values and `finalize_static_fetch_result` wiring
- Validation: unit tests in `tests/unit/orchestrator/test_failure_classification.py` and
  `tests/unit/orchestrator/test_html_quality.py`

### M3 — Browser seam (reserved)

- Implement `BrowserFetchBackend` and optional second fetch mode on `fetch_job.mode`
- Validation: integration test with mocked backend producing a snapshot

## 7. Implementation log

- 2026-05-12: Initial diagnostics endpoint, metrics label, static HTML gate, and protocol seam.
- 2026-05-12: Weak static HTML retains raw ``content_snapshot`` bytes with ``eligible_for_evidence_parse``
  trace gating; browser fallback consults ``trace_json``; parse batch exposes ``skipped_static_html_hold``.

## 8. Risks / deferred work

- HTML heuristics can false-positive on legitimate minimal pages; thresholds stay conservative.
- Parser rejection distribution depends on `task_event` payloads retaining `parse_decisions`.

## 9. Rollback

- Revert acquisition and HTTP client modules and remove the new route to restore legacy
  `error_code` strings (`too_many_redirects`, generic `network_error` for all transport errors).
