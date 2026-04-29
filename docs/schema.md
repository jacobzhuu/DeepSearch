# Schema

## Phase status
Phase 11 still uses the reversible Phase 1 plus Phase 2 research ledger schema, plus two minimal follow-on migrations for Phase 4 acquisition and Phase 5 parsing provenance, and one Phase 10 report-artifact hardening migration, through Alembic and SQLAlchemy 2.x. Phase 6, Phase 7, Phase 8, and Phase 9 introduced indexing, retrieval, claim-drafting, verification, and report-synthesis seams without a new database migration; Phase 10 adds only the minimum `report_artifact` provenance fields required for operational hardening, and Phase 11 adds no schema change at all. The current implementation covers the following entities:

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

## Phase 11 schema note

- Phase 11 adds no new relational fields, tables, or indexes
- Research Planner v1 stores its output in existing `task_event.payload_json` rows with event types `research_plan.created` and `research_plan.failed`; no `research_plan` table or migration exists
- planner guardrail, source-selection, answer-slot coverage, source-yield, evidence-yield, dropped-source, verifier-detail, supplemental-acquisition, and failure-diagnostic fields are stored in existing `task_event.payload_json`, `claim.notes_json`, `report_artifact.manifest_json`, and API observability payloads; no new planner, source-quality, answer-slot, evidence-candidate, or acquisition-retry table exists
- query-aware claim ranking stores deterministic scoring metadata in the existing `claim.notes_json` field, including `claim_category`, `answer_role`, `answer_relevant`, `content_quality_score`, `query_relevance_score`, `claim_quality_score`, `query_answer_score`, `source_quality_score`, `claim_selection_score`, `rejected_reason`, `draft_mode`, `fallback_reason`, and `original_rejected_reason`
- claim drafting now also stores code-contract lineage fields in `claim.notes_json`, including `slot_ids`, `source_document_id`, `source_chunk_id`, `citation_span_id`, `claim_evidence_id`, `source_intent`, `evidence_candidate_id`, `evidence_quality_score`, `evidence_salience_score`, `evidence_rejection_reasons`, and a serialized `evidence_candidate` payload
- verification stores deterministic lexical verifier metadata in `claim.notes_json["verification"]`, including `verifier_method`, strong and weak support counts, contradiction counts, insufficient-evidence count, relation details, shallow-overlap flags, numeric/date mismatch flags, and scope-mismatch flags
- host-local operational closeout, optional compose wiring, init scripts, and smoke validation all reuse the existing Phase 10 schema as-is
- `services/orchestrator/app/research_quality/` provides the current code-level source-intent, answer-slot, evidence-candidate, source-yield, evidence-yield, dropped-source reason, and slot-coverage contracts; these are not relational schema entities yet
- the stable code-level diagnostics field names are `selected_sources`, `attempted_sources`, `dropped_sources`, `source_yield_summary`, `evidence_yield_summary`, `slot_coverage_summary`, and `verification_summary`; older rows that lack these fields are interpreted as empty summaries rather than requiring a data migration
- the latest relational schema change remains `20260424_0005_report_artifact_manifest_and_hash`
- the current functional ledger loop is complete through:
  - `research_task -> search_query -> candidate_url -> fetch_job/fetch_attempt -> content_snapshot -> source_document/source_chunk -> claim/citation_span/claim_evidence -> report_artifact`
- no additional schema has been introduced for:
  - OpenClaw
  - HTML/PDF artifacts
  - persisted planner / gap analyzer entities
  - richer verifier workflows
  - advanced retrieval optimization

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
  - `source_document` stores per-task canonical source records, a minimal `content_snapshot` provenance link, and source scoring fields
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
  - `fetch_job(candidate_url_id, mode)`
  - `fetch_attempt(fetch_job_id, attempt_no)`
  - `content_snapshot(fetch_attempt_id)`
  - `source_document(task_id, canonical_url)`
  - `source_document(content_snapshot_id)`
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
- the product pipeline run endpoint now uses the already-reserved runtime statuses for synchronous execution progress:
  - `RUNNING`
  - `SEARCHING`
  - `ACQUIRING`
  - `PARSING`
  - `INDEXING`
  - `DRAFTING_CLAIMS`
  - `VERIFYING`
  - `REPORTING`
  - `COMPLETED`
  - `FAILED`

## Phase 3 search-discovery usage

- Phase 3 adds no new tables and no new schema migration; it activates the existing `search_query` and `candidate_url` tables through repositories, services, and API routes
- each executed expanded query persists one `search_query` row linked to a revision-scoped `research_run`
- the first search for a task creates `research_run.round_no = 1`; the first search after `revise` increments `revision_no` creates the next run round
- `search_query.raw_response_json` currently stores:
  - `task_revision_no`
  - `expansion_kind`
  - `expansion_metadata`
  - `source_engines`
  - `response_metadata`
  - `result_count`
- `candidate_url` intake is Phase 3 task-scoped and service-level:
  - canonicalize URL
  - evaluate allow or deny domain rules
  - skip duplicates already persisted for the same task
  - insert only the first accepted canonical URL record
- `candidate_url.selected` remains `false` by default in Phase 4 because no fetch-selection or scheduling policy exists yet
  - this remains true in Phase 6; parsing and indexing do not implicitly mark URLs as selected
- `candidate_url.metadata_json` currently stores:
  - `provider`
  - `source_engine`
  - `snippet`
  - `result_metadata`
  - `task_revision_no`
  - `expansion_kind`
  - `expansion_metadata`
  - `query_text`
- explicit provenance TODO:
  - a future phase may need a `search_query_candidate_url` association table, or an equivalent relation, to model one canonical URL being surfaced by multiple `search_query` rows without overloading the current minimum Phase 3 shape

## Phase 4 acquisition usage

- Phase 4 adds one migration, `20260423_0003`, which introduces `uq_fetch_job_candidate_url_id_mode`
- `fetch_job.mode` is currently `HTTP` only
- `fetch_job(candidate_url_id, mode)` is the current idempotency boundary for synchronous acquisition
- `fetch_attempt` currently records a single attempt per `fetch_job`; retries are deferred to a later phase
- `fetch_attempt.trace_json` now carries the minimum acquisition trace, including final URL, redirect chain, resolved IPs, byte counts, and explicit policy or storage failure details when applicable
- `content_snapshot.storage_bucket` and `content_snapshot.storage_key` currently point to an object-store seam that can be backed by either the local filesystem or MinIO
- `content_snapshot.content_hash` is stored as `sha256:<hex>`

## Phase 5 parsing usage

- Phase 5 adds one migration, `20260423_0004`, which introduces `source_document.content_snapshot_id`
- `source_document.content_snapshot_id` is the current minimum provenance seam from parsed text back to the exact `content_snapshot` used for extraction
- `source_document` is currently a current-state record, not a parse-history version chain
  - when later parsing targets the same `(task_id, canonical_url)`, the existing row is updated in place and its chunks are rebuilt
  - later phases may introduce explicit parse history or versioned source-document lineage, but that does not exist yet
- the current parser only accepts `content_snapshot` rows whose `fetch_job.status = SUCCEEDED` and whose `fetch_attempt.error_code IS NULL`
- supported MIME types are currently:
  - `text/html`
  - `text/plain`
- unsupported MIME types are skipped explicitly at service and API level; no `source_document` row is created for them in the current phase
- Phase 5 parsing does not add a new parse-job ledger table; skip and failure reasons are returned by the parse command response only
- parse responses currently use this stable `reason` enum:
  - `fetch_not_succeeded`
  - `already_parsed`
  - `snapshot_object_missing`
  - `unsupported_mime_type`
  - `empty_extracted_text`
- `source_document` is still unique on `(task_id, canonical_url)`
  - when a later parse targets the same canonical URL for the same task, the current minimum behavior is to update the existing `source_document`, move its `content_snapshot_id` to the new snapshot, and rebuild `source_chunk` rows
- `source_document.source_type` currently uses the minimum parser-facing values:
  - `web_page` for `text/html`
  - `plain_text` for `text/plain`
- `source_chunk.token_count` is currently a stable approximate token count derived from character length
- `source_chunk.metadata_json` currently stores:
  - `strategy`
  - `char_count`
  - `paragraph_count`
  - `approx_token_count`
  - `content_snapshot_id`
  - `mime_type`
  - `extractor`
  - `extractor_strategy_used`
  - `fallback_used`
  - `removed_boilerplate_count`
  - `extracted_text_length`
  - `text_cleanup_applied`
  - `dropped_broken_link_fragments`
  - `preserved_link_text_count`
  - `link_text_extraction_strategy`
- the parser uses those metadata keys for HTML extractor observability only; no parse-history table or new relational schema was added
- `reference_section` chunk metadata is reserved for chunks that begin with reference material or are mostly citation/reference text; a chunk with explanatory body prose followed by `See also` or `References` should remain claim-eligible when its quality score passes

## Phase 6 indexing and retrieval usage

- Phase 6 adds no new tables and no new Alembic revision; the indexing seam is external to the relational ledger
- the current index backend writes one document per `source_chunk`, using deterministic `source_chunk_id` identity for idempotent upsert behavior
- indexed chunk payloads currently contain:
  - `task_id`
  - `source_document_id`
  - `source_chunk_id`
  - `canonical_url`
  - `domain`
  - `chunk_no`
  - `text`
  - `metadata`
- the current OpenSearch write path is task-scoped but does not create a separate relational index-job ledger table
- retrieval is currently task-scoped and explainable:
  - filter by exact `task_id`
  - run a simple text `match` over indexed chunk `text`
  - sort primarily by score, then by stable chunk identity fields
- Phase 6 does not create claim, citation-span binding, verification, or report-generation state

## Phase 7 claim drafting and citation binding usage

- Phase 7 adds no new tables and no new Alembic revision; it activates the existing `claim`, `citation_span`, and `claim_evidence` tables through services and APIs
- current draft claims are deliberately minimal:
  - `claim_type` currently uses the stable singleton set `fact`
  - `verification_status` currently uses the stable draft-only value `draft`
- current claim evidence is deliberately minimal:
  - `relation_type` currently uses the stable singleton set `support`
  - contradiction or mixed evidence handling is deferred to the verifier phase
- citation binding is now explicitly validated at service level before persistence:
  - `start_offset >= 0`
  - `end_offset > start_offset`
  - `end_offset <= len(source_chunk.text)`
  - `excerpt == source_chunk.text[start_offset:end_offset]`
- `citation_span.normalized_excerpt_hash` is now derived from a normalized excerpt string and stored with a `sha256:<hex>` format
- current claim drafting is deterministic and explainable:
  - select source chunks from retrieval hits or explicit ids
  - generate sentence candidates from eligible chunks
  - classify query intent with lightweight rules, including the `What is X and how does it work?` definition/mechanism pattern
  - score each candidate for content quality, query relevance, claim quality, answer fit, and source quality
  - reject setup/getting-started instructions and broken-link residue before persistence
  - select top answer-relevant candidates with light category and paragraph diversification
  - if strict filters produce no claims, optionally select only explanatory fallback candidates that still meet minimum query-answer or query-relevance thresholds
  - normalize that span into a claim statement
  - create or reuse the exact-statement task claim
  - create or reuse the exact-offset citation span
  - create or reuse `claim_evidence(claim_id, citation_span_id, support)`
- no-claims drafting diagnostics are carried in service return payloads and `pipeline.failed.details`, not stored in new database tables
- repeated Phase 7 draft calls are guarded by exact statement reuse plus existing citation and claim-evidence uniqueness boundaries
- broader answer-level near-duplicate suppression is deterministic and service-level only; diagnostics report `near_duplicate_claims_removed`, while exact duplicate statements can still reuse the same claim and add additional citation evidence

## Phase 8 verification and conflict-handling usage

- Phase 8 adds no new tables and no new Alembic revision; it continues to use the existing `claim`, `citation_span`, and `claim_evidence` tables through services and APIs
- Phase 8 verification expands the stable service-level verification status set to:
  - `draft`
  - `supported`
  - `mixed`
  - `unsupported`
- Phase 8 verification expands the stable service-level claim evidence relation set to:
  - `support`
  - `contradict`
- verification remains ledger-first and reuses the current seams:
  - retrieve task-scoped candidate chunks by `claim.statement`
  - select one best sentence-like span per candidate chunk
  - validate the exact offsets and excerpt against `source_chunk.text`
  - create or reuse the exact-offset `citation_span`
  - create or reuse `claim_evidence(claim_id, citation_span_id, relation_type)`
- verification aggregates the current evidence bundle back onto the claim through `claim.notes_json["verification"]`, which now stores the minimum explainable bundle:
  - `method`
  - `verification_query`
  - `support_evidence_count`
  - `contradict_evidence_count`
  - `rationale`
- the current verification resolution is deliberately minimal:
  - `supported` when support evidence exists and contradict evidence does not
  - `mixed` when both support and contradict evidence exist
  - `unsupported` otherwise
- repeated Phase 8 verification calls are guarded only by exact citation-span reuse plus the existing `claim_evidence(claim_id, citation_span_id, relation_type)` uniqueness boundary; richer semantic deduplication is deferred
- no Phase 8 migration was added; the current stable verification and relation enums are enforced by service and API contracts rather than database check constraints

## Phase 9 report synthesis usage

- Phase 9 introduced report synthesis on top of the existing `report_artifact` table through services and APIs
- the current report artifact contract is deliberately minimal:
  - `format` is `markdown` only
  - the stored object is a Markdown document synthesized from the persisted task, claim, citation, and verification ledger
- report generation reuses the existing filesystem-backed object-store abstraction from earlier phases
  - current artifacts are stored under the configured report bucket, which defaults to `reports`
  - current artifact keys use the stable shape `<task_id>/v<version>/report.md`
- the current report content is evidence-first and deterministic:
  - supported claims appear as settled conclusions
  - mixed and unsupported claims are rendered only with explicit status labels and uncertainty sections
  - no new claims, verification decisions, or retrieval operations are introduced during report generation
  - low-quality or off-query supported claims are filtered using persisted or recomputed claim-quality and query-answer scores before they can appear in the Executive Summary, Answer sections, Evidence Table, or claim evidence mapping
  - definition/mechanism reports also apply an answer-category gate so supported `other`, setup, community, slogan, reference, or navigation claims do not appear as conclusions by status alone
  - the rendered Source Scope and Limitations section reports answer-relevant included claims and excluded low-quality/off-query claims
- repeated report generation is currently guarded by byte-for-byte reuse of the latest stored Markdown artifact
  - if the newly rendered Markdown matches the latest stored artifact bytes, no new `report_artifact` row is created
  - otherwise a new Markdown artifact version is created for the task
- `GET /report` reads the stored artifact object and returns artifact metadata plus Markdown bytes
  - it does not live re-render the report from the current ledger on read
- `report_artifact` remains the only relational ledger for report outputs in the current phase
  - there is no separate report-job table
  - there is no HTML or PDF artifact in the current phase
- the current Markdown report includes the minimum required sections:
  - title
  - research question
  - executive summary
  - answer
  - evidence table
  - source scope and limitations
  - unresolved / low coverage areas
  - appendix: claim evidence mapping

## Phase 10 infrastructure-hardening usage

- Phase 10 adds one migration, `20260424_0005`, which introduces:
  - `report_artifact.content_hash`
  - `report_artifact.manifest_json`
- `report_artifact.content_hash` now stores the Markdown artifact digest as `sha256:<hex>`
  - historical Phase 9 rows may still have `NULL` content hashes until they are regenerated
- `report_artifact.manifest_json` now stores the minimum provenance snapshot used to synthesize the Markdown artifact:
  - `manifest_version`
  - `task_id`
  - `revision_no`
  - `query`
  - `report_title`
  - `claim_counts`
  - `answer_slot_coverage`
  - `claim_snapshot`
  - `source_snapshot`
- repeated report generation can now short-circuit on the latest artifact when the newly rendered Markdown still matches the latest stored content hash and bytes
- `GET /report` now verifies the stored Markdown bytes against `report_artifact.content_hash` when that hash is present
  - a mismatch is treated as artifact integrity failure rather than silently serving drifted content
- the object-store seam now supports two backends:
  - `filesystem`
  - `minio`
- MinIO startup validation is bucket-aware in Phase 10
  - the configured snapshot and report buckets must exist at startup when `SNAPSHOT_STORAGE_BACKEND=minio`
- the OpenSearch chunk index remains external to the relational schema
  - Phase 10 adds no relational index-job tables
  - Phase 10 only strengthens live validation and error handling around the existing index seam

## Deferred beyond Phase 10

- `research_plan`, `attachment`, and `domain_policy`
- many-to-many provenance between one canonical URL and every query that surfaced it, likely via a `search_query_candidate_url` style association table
- browser fallback, fetch scheduling, retries, and queue execution
- parse-history-aware `source_document` versioning
- multi-span claim synthesis, richer contradiction reasoning, and richer report-generation behavior
- Tika parsing, embeddings, and advanced retrieval behavior
- additional task APIs beyond the thin Phase 2 plus Phase 3 surface and any push-based event delivery
