# code_review.md

This document defines the review standard for this repository.

The project is a Deep Research / OSINT platform. Review quality is judged primarily by:

1. correctness
2. evidence traceability
3. resumability
4. operational safety
5. clarity of operator understanding

Not by:
- code volume
- apparent sophistication
- extra abstraction without need

---

## 1. Review goals

Every review should try to catch:

- incorrect behavior
- broken architecture boundaries
- silent contract changes
- missing evidence / provenance
- migration risk
- unsafe side effects
- weak testing
- stale or missing docs

---

## 2. Severity levels

Use these severities:

### Critical
Would likely cause data loss, unsafe execution, broken ledger integrity, severe security exposure, or unrecoverable workflow corruption.

### High
Would likely break a key phase path, produce misleading claims, break task lifecycle semantics, or create major rollback pain.

### Medium
Would likely cause maintainability, observability, or operator usability issues, or leave correctness partially unverifiable.

### Low
Style, consistency, naming, or minor ergonomics issues with low operational risk.

---

## 3. Review output format

Use this format:

### Summary
Short overall assessment.

### Findings
For each finding:
- severity:
- area:
- file(s):
- issue:
- why it matters:
- recommended change:

### Validation gaps
List what was not tested or not proven.

### Decision
One of:
- approve
- approve with follow-ups
- request changes

---

## 4. Architecture review checklist

Check whether the change preserves the intended layering:

- UI / gateway layer
- orchestrator / workflow layer
- acquisition / parsing / indexing layer
- reporting / delivery layer

Flag if:
- business logic leaks into UI glue
- crawl / parse details leak into unrelated layers
- reporting logic bypasses ledger or claim evidence
- chat façade becomes the hidden primary product path

---

## 5. Workflow and state review checklist

When task lifecycle logic changed, verify:

- states are explicit
- transitions are valid
- task events are emitted where needed
- pause / resume semantics remain coherent
- failures distinguish transient vs terminal
- checkpoint / resume behavior is not broken
- cancel does not allow new unsafe side effects after cancellation

Flag any hidden or undocumented state.

---

## 6. Ledger and provenance review checklist

When schema or evidence logic changed, verify:

- URL identity is canonicalized
- fetch attempts are recorded, not just final success
- snapshots remain traceable to attempts
- source documents and chunks remain reconstructible
- citation spans remain linked to claims
- report artifacts remain traceable to task/run/claims
- provenance is not collapsed into vague JSON blobs without reason

Flag any change that weakens auditability.

---

## 7. Claim and evidence review checklist

When claim drafting or verification changed, verify:

- supported claims have supporting evidence
- conflicting evidence is not silently ignored
- unsupported claims are not disguised as facts
- confidence and verification status remain meaningful
- the system does not optimize for “complete-looking narrative” over correctness

Flag any overclaiming.

---

## 8. Acquisition and parsing review checklist

When search / crawl / parse changed, verify:

- SSRF protections are preserved
- private / loopback / metadata targets remain blocked
- MIME restrictions remain coherent
- timeouts and retry behavior are explicit
- large-file / malformed-file risks are handled
- browser fallback does not silently widen unsafe access
- extraction failure is observable and recorded

Flag any hidden network broadening.

---

## 9. Database and migration review checklist

When migrations are introduced, verify:

- migration exists and is reversible when practical
- constraints and indexes match the intended access pattern
- nullable vs non-nullable choices are justified
- enum / state changes are consistent with application code
- rollout and rollback are documented
- data backfill requirements are explicit

Flag any schema change with unclear operational consequences.

---

## 10. API review checklist

When API or contract changes occur, verify:

- request / response behavior is documented
- status codes are coherent
- compatibility expectations are explicit
- task APIs preserve async semantics
- façade APIs do not obscure the real task model
- error handling is machine-readable where needed

Flag silent contract drift.

---

## 11. Testing review checklist

Check whether the change added or updated appropriate tests.

Expected categories as applicable:

- unit tests
- repo / service tests
- migration tests
- integration tests
- narrow e2e validation

Flag when:
- behavior changed without tests
- tests exist but do not prove the risky path
- the author claims validation without actually running commands

---

## 12. Documentation review checklist

When behavior changed, verify updates to the right docs:

- `Deep Research Codex Dev Spec.md`
- `PLANS.md`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`

Flag code that changes operator behavior but leaves docs stale.

---

## 13. Dependency review checklist

When dependencies change, verify:

- the dependency is necessary
- version pinning is appropriate
- production vs dev dependency is correct
- there is no simpler in-repo alternative
- security or maintenance implications are acknowledged

Flag speculative dependencies.

---

## 14. What “good” looks like in this repository

A good change usually has these properties:

- minimal but sufficient
- phase-appropriate
- explicitly validated
- ledger-safe
- reversible
- documented
- honest about limits

A bad change often looks like:

- broad but weakly justified
- hidden schema shortcuts
- unverifiable “smart” automation
- missing evidence links
- missing state-transition reasoning
- docs and tests left behind

---

## 15. Default reviewer posture

Prefer conservative review.

If you are unsure whether something is safe, ask:

- does this preserve traceability?
- does this preserve resumability?
- does this preserve operator clarity?
- does this preserve rollback ability?

If not clearly yes, do not wave it through.