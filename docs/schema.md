# Schema

## Phase 1 status

Phase 1 introduces the first reversible research ledger schema through Alembic and SQLAlchemy 2.x. The current implementation covers the following entities:

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
  - `research_task` stores query, status, priority, constraints, and task timing
  - `research_run` stores round number, current state, and checkpoint payload
  - `task_event` stores auditable task and run events
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
- supporting indexes exist on common lookup paths such as task status, event ordering, fetch scheduling, source ranking, and claim/report retrieval

## Deferred beyond Phase 1

- `research_plan`, `attachment`, and `domain_policy`
- task API endpoints and event-stream delivery
- runtime task orchestration and fetch/search behavior
