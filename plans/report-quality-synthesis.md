# Report Quality Synthesis Increment

## 1. Objective

Improve deterministic Markdown report quality by adding inline evidence citations, basic redundancy control, grounded synthesis planning, and a structured critic pass over the first draft.

## 2. Why this exists

Reports should read like evidence-backed research synthesis rather than a flat list of claim summaries. The change must preserve the existing ledger-first contract: only persisted verified claims and evidence can support factual report prose.

## 3. Scope

In scope:
- inline evidence anchors for factual report paragraphs and bullets
- near-duplicate claim clustering before report writing
- deterministic synthesis bullets grouped by theme and grounded by claim/evidence ids
- deterministic structured critic diagnostics for citation gaps, redundancy, shallow sections, unsupported paragraphs, and missing synthesis
- focused unit tests and docs updates

Out of scope:
- search, fetch, parse, index, claim drafting, or verification behavior changes
- new dependencies
- schema migrations
- LLM-generated unsupported report facts

## 4. Constraints

- Use existing `claim`, `claim_evidence`, `citation_span`, `source_chunk`, `source_document`, and `report_artifact.manifest_json` seams.
- Do not add production dependencies.
- Do not persist new relational tables.
- Quantitative, comparative, and factual rendered statements must have evidence anchors when possible.
- Weak or unsupported material may appear only as uncertainty or limitation language.

## 5. Relevant files and systems

- `services/orchestrator/app/services/reporting.py`
- `services/orchestrator/app/reporting/markdown.py`
- `services/orchestrator/app/reporting/manifest.py`
- `docs/schema.md`
- `docs/runbook.md`
- `tests/unit/orchestrator/test_report_synthesis_service.py`

`services/orchestrator/app/services/report_synthesis_service.py` was requested for inspection but is not present in this tree.

## 6. Milestones

Milestone 1: Planning primitives
- Add claim canonicalization, duplicate clustering, synthesis grouping, and critic JSON helpers.
- Validate with focused unit tests.

Milestone 2: Renderer integration
- Render inline evidence anchors and evidence footnotes.
- Use primary claims once and reduce duplicate later mentions.
- Validate with report synthesis tests.

Milestone 3: Manifest/docs
- Store synthesis/critic diagnostics in existing manifest JSON.
- Update schema/runbook notes.

## 7. Implementation log

- 2026-05-14: Started narrow deterministic implementation. Confirmed requested `report_synthesis_service.py` is missing; using `services/orchestrator/app/services/reporting.py`.
- 2026-05-14: Added deterministic inline evidence footnotes, duplicate claim clustering, synthesis-plan generation, and structured critic diagnostics. Reporting tests pass locally for the focused file.

## 8. Validation

- `pytest tests/unit/orchestrator/test_report_synthesis_service.py -q` — passed
- `pytest tests/unit/orchestrator/test_task_observability.py -q` — passed
- `pytest tests/unit/orchestrator -q` — passed
- `ruff check` on affected files — passed

## 9. Risks and unknowns

- Existing tests may assert exact report prose; inline anchors can require small expectation updates.
- Deterministic relationship inference is conservative and intentionally shallow compared with a full analyst model.

## 10. Rollback / recovery

Revert changes to reporting service, Markdown renderer, manifest docs, tests, and this plan. No data migration or destructive recovery is required.

## 11. Deferred work

- Optional LLM critic/revision stage, still constrained to verified claim/evidence ids.
- Richer relationship inference and section-specific narrative planning.
