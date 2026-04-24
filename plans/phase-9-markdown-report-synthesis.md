# Phase 9 Markdown Report Synthesis

## 1. Objective

Implement the first report-synthesis slice on top of the existing task, claim, citation, and verification ledger: render a deterministic Markdown report, persist it as a `report_artifact`, and expose thin APIs to generate and read the latest report artifact.

## 2. Why this exists

Phase 8 can now draft and verify claims with traceable citation evidence, but the system still cannot turn that persisted evidence bundle into an operator-facing report artifact. Phase 9 needs the thinnest possible report path so later export and delivery phases build on stored Markdown artifacts instead of skipping from verified claims directly to presentation-specific formats.

## 3. Scope

### In scope

- add a deterministic Markdown report synthesis helper
- build report content strictly from existing:
  - `research_task`
  - `claim`
  - `citation_span`
  - `claim_evidence`
  - `verification_status`
- persist Markdown output through the existing object-store abstraction and `report_artifact` ledger
- add thin APIs for:
  - `POST /api/v1/research/tasks/{task_id}/report`
  - `GET /api/v1/research/tasks/{task_id}/report`
- include the minimum required Markdown sections:
  - title
  - research question
  - executive summary
  - method and source scope
  - key conclusions
  - conclusion details and evidence
  - conflicts / uncertainty
  - unresolved questions
  - appendix: source list
  - appendix: claim to citation-span mapping
- keep mixed and unsupported claims explicitly labeled in the report
- add repository, helper, service, API, and narrow integration tests
- update docs and this plan

### Out of scope

- HTML or PDF export
- complex templating systems
- new verification logic
- planner or gap-analyzer behavior
- new retrieval, search, or acquisition logic
- additional report formats or messaging delivery

## 4. Constraints

- stay strictly within Phase 9
- preserve current Phase 2 through Phase 8 behavior and additive compatibility
- synthesize reports strictly from existing persisted claim and evidence state
- do not generate unsupported conclusions with no evidence
- mixed and unsupported claims must be explicitly labeled, never presented as settled facts
- keep report storage inside the existing object-store abstraction
- keep the output deterministic and explainable
- do not add a new production dependency

## 5. Relevant files and systems

- `services/orchestrator/app/services/`
- `services/orchestrator/app/api/routes/`
- `services/orchestrator/app/api/schemas/`
- `services/orchestrator/app/reporting/`
- `services/orchestrator/app/storage/`
- `services/orchestrator/app/main.py`
- `services/orchestrator/app/settings.py`
- `packages/db/repositories/reports.py`
- `packages/db/repositories/claims.py`
- `packages/db/repositories/sources.py`
- `packages/db/models/ledger.py`
- `tests/unit/db/`
- `tests/unit/orchestrator/`
- `services/orchestrator/tests/`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/architecture.md`
- `docs/phases/phase-9.md`

## 6. Milestones

### Milestone 1
- intent: define the deterministic Markdown report structure and minimal artifact-storage seam
- code changes:
  - reporting helper module
  - report artifact repository helpers if needed
  - helper unit tests
- validation:
  - helper-focused unit tests

### Milestone 2
- intent: persist and retrieve Markdown report artifacts through a minimal reporting service
- code changes:
  - reporting service
  - object-store reuse for reports
  - service and repository tests
- validation:
  - repo tests
  - service tests

### Milestone 3
- intent: expose the minimal report generation and retrieval APIs
- code changes:
  - route and schema wiring
  - app router update
  - API tests
- validation:
  - API tests

### Milestone 4
- intent: keep docs synchronized and validate the full narrow report path
- code changes:
  - update architecture, API, schema, runbook, phase doc, and this plan
- validation:
  - lint, format, type checks, full pytest
  - narrow manual flow: create -> draft claims -> verify claims -> report -> get report

## 7. Implementation log

- 2026-04-24 research: reread `AGENTS.md`, `deep_research_codex_dev_spec.md`, `PLANS.md`, `code_review.md`, and current docs. Confirmed Phase 9 should stop at minimal Markdown report synthesis on top of the existing claim and evidence ledger, without HTML/PDF export, planner work, or any new retrieval or acquisition logic.
- 2026-04-24 implementation:
  - added `services/orchestrator/app/reporting/markdown.py` with deterministic Markdown rendering for the minimum Phase 9 section set
  - added `services/orchestrator/app/services/reporting.py` and thin report APIs for `POST /report` and `GET /report`
  - reused the existing object-store abstraction for Markdown artifact persistence under the configured report bucket
  - extended repository seams so report synthesis can read claim evidence together with `citation_span -> source_chunk -> source_document` provenance
  - added idempotent byte-for-byte artifact reuse so repeated report generation does not create a new `report_artifact` row when Markdown content is unchanged
  - tightened `GET /report` so it returns stored artifact metadata plus stored Markdown bytes only, instead of mixing artifact content with newly computed ledger summaries
  - added helper, repository, service, and API tests covering deterministic report content, stored-artifact reuse, and non-drifting read semantics
  - updated `docs/api.md`, `docs/schema.md`, `docs/runbook.md`, `docs/architecture.md`, and `docs/phases/phase-9.md`

## 8. Validation

- completed:
  - `python3 -m ruff check .`
  - `python3 -m black --check .`
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit`
  - `python3 -m pytest`
  - `DATABASE_URL=sqlite:////tmp/deepresearch_phase9_manual.db python3 -m alembic -c alembic.ini upgrade head`
  - narrow manual report-generation path against temporary SQLite plus the current FastAPI API surface
- manual-path outcome:
  - `POST /report` returned `200`, `version = 1`, `reused_existing = false`, `supported_claims = 1`, `unsupported_claims = 1`
  - `GET /report` returned `200` and the same `report_artifact_id`
  - `GET /report` no longer exposes synthesis count fields that can drift from the stored artifact
  - the stored Markdown contained the required executive-summary and claim-to-citation appendix sections
  - the Markdown explicitly labeled the unsupported claim
  - the artifact file was written to `/tmp/deepresearch_phase9_snapshots/reports/<task_id>/v1/report.md`

- known unvalidated areas before implementation:
  - real OpenSearch cluster behavior
  - PostgreSQL-specific behavior
  - docker-based validation because `docker` is unavailable in the current environment

## 9. Risks and unknowns

- report text must remain evidence-first; weak summarization logic could accidentally overclaim
- reusing the snapshot object-store abstraction for reports must remain explicit in docs so operators understand where Markdown artifacts are stored
- repeated report generation should avoid unnecessary duplicate artifact versions when the rendered Markdown has not changed
- the current `report_artifact` schema has no content hash column, so any idempotency guard will need to compare stored bytes or document explicit versioning behavior

## 10. Rollback / recovery

- revert the Phase 9 helper, service, route, schema, test, and doc changes
- if a migration is introduced, document the exact downgrade command and data caveats before finishing the turn

## 11. Deferred work

- HTML and PDF rendering
- richer report layouts and templating
- planner-driven “next questions” synthesis
- report delivery through separate services or gateways
- provenance extensions if later phases need report-to-run or report-to-claim snapshot pinning beyond the current artifact ledger
