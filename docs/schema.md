# Schema

## Phase 1 status
Phase 2 uses the Phase 1 reversible research ledger schema through Alembic and SQLAlchemy 2.x. The current implementation covers the following entities:

- `research_task`
- `research_run`
- `task_event`
- `search_query`
- `candidate_url`
- `fetch_job`
- `fetch_attempt`
- `content_snapshot`
- `source_document`
- `source_chunk`
- `citation_span`
- `claim`
- `claim_evidence`
- `report_artifact`

## Current schema shape

- task lifecycle foundation:
  - `research_task` stores query, status, priority, constraints, `revision_no`, `last_event_sequence_no`, and task timing
  - `research_run` stores round number, current state, and checkpoint payload
  - `task_event` stores auditable task and run events, including per-task `sequence_no`
- search and fetch ledger:
  - `search_query` stores issued queries per task run
  - `candidate_url` stores canonicalized search candidates per search query
  - `fetch_job` stores planned fetch work records
  - `fetch_attempt` stores each fetch attempt
  - `content_snapshot` stores the traceable content object reference per fetch attempt
- source and citation ledger:
  - `source_document` stores per-task canonical source records and source scoring fields
  - `source_chunk` stores chunked source text
  - `citation_span` stores excerpt-level traceability within a chunk
- claim and report ledger:
  - `claim` stores drafted claims and verification metadata fields
  - `claim_evidence` links claims to citation spans
  - `report_artifact` stores report object references by task and version

## Constraints and indexes

- `research_task.status` and `research_run.current_state` are constrained to the explicit task states defined in the product spec
- the schema includes unique constraints for key provenance boundaries such as:
  - `research_run(task_id, round_no)`
  - `candidate_url(search_query_id, canonical_url)`
  - `fetch_attempt(fetch_job_id, attempt_no)`
  - `content_snapshot(fetch_attempt_id)`
  - `source_document(task_id, canonical_url)`
  - `source_chunk(source_document_id, chunk_no)`
  - `citation_span(source_chunk_id, start_offset, end_offset)`
  - `claim_evidence(claim_id, citation_span_id, relation_type)`
  - `report_artifact(task_id, version, format)`
- `task_event` now has a stable per-task uniqueness boundary:
  - `task_event(task_id, sequence_no)`
- supporting indexes exist on common lookup paths such as task status, event ordering, fetch scheduling, source ranking, and claim/report retrieval

## Phase 2 task-event usage

- Phase 2 still uses the writable `research_task` status subset `PLANNED`, `PAUSED`, and `CANCELLED`
- the schema and code now reserve these later runtime-facing statuses for future phases:
  - `QUEUED`
  - `RUNNING`
  - `FAILED`
  - `COMPLETED`
  - `NEEDS_REVISION`
- Phase 2 emits these stable `task_event.event_type` values:
  - `task.created`
  - `task.paused`
  - `task.resumed`
  - `task.cancelled`
  - `task.revised`
- `research_task.revision_no` starts at `1` and increments only on `revise`
- `task_event.sequence_no` starts at `1` per task and defines the stable `/events` ordering contract
- Phase 2 event payloads use a stable minimum JSON structure:
  - `event_version`
  - `source`
  - `from_status`
  - `to_status`
  - `changes`

## Deferred beyond Phase 2

- `research_plan`, `attachment`, and `domain_policy`
- additional task APIs beyond the thin Phase 2 surface and any push-based event delivery
- runtime task orchestration and fetch/search behavior
