# Phase 9

## Goal

Introduce the first report-synthesis slice of the orchestrator: reuse the persisted task, claim, citation, evidence, and verification ledger to render a deterministic Markdown report, store it as a `report_artifact`, and expose thin APIs to generate and read the latest report artifact.

## Deliverables

- minimal Markdown report rendering helper seam
- thin repository, service, and API paths for:
  - `POST /report`
  - `GET /report`
- reuse of the existing object-store abstraction for report artifact storage
- report sections for:
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
- explicit labeling of `mixed`, `unsupported`, and `draft` claims in the report body
- tests for helper behavior, repository helpers, service behavior, API contracts, and narrow integration
- updated architecture, API, schema, runbook, and plan documentation

## Explicitly excluded

- HTML or PDF export
- complex templating systems
- new verifier logic
- multi-round planner or gap-analyzer logic
- complex multi-model voting
- new retrieval, search, or acquisition logic
