# AGENTS.md

## Project identity

This repository implements a **Deep Research / OSINT research platform** for Linux servers.

Primary product semantics:

- asynchronous `research_task`
- resumable long-running workflow
- evidence-first claim generation and verification
- auditable research ledger
- web workspace first, optional messaging gateway later

This is **not**:
- a generic chatbot shell
- a simple RAG demo
- an agent playground with unclear boundaries

---

## Read this first

Before making any change, read these files in order:

1. `Deep Research Codex Dev Spec.md`
2. `PLANS.md`
3. `code_review.md`
4. `docs/architecture.md` if present
5. `docs/api.md` if present
6. `docs/schema.md` if present
7. `docs/runbook.md` if present

If any of these files are missing, say so explicitly in your response and proceed with the minimum safe assumption.

---

## Core implementation principles

1. Keep the system centered on `research_task`, not `chat`.
2. Treat the research ledger as first-class data, not an afterthought.
3. Prefer minimal runnable increments over broad speculative scaffolding.
4. Preserve architecture boundaries:
   - UI / gateway layer
   - orchestrator / workflow layer
   - acquisition / parsing / indexing layer
   - reporting / delivery layer
5. Every important claim must be traceable to evidence.
6. Every important side effect must be idempotent or explicitly guarded.
7. Do not silently expand scope.

---

## Phase discipline

Work strictly by phase.

Rules:

- Do **not** skip phases.
- Do **not** implement future-phase features “while nearby”.
- Do **not** refactor unrelated code unless required to unblock the current phase.
- If a current task depends on future infrastructure, create the thinnest acceptable seam and document the deferred work.

When unclear, choose the smallest implementation that keeps the architecture correct.

---

## Required delivery format for every implementation turn

Use this exact structure:

1. Goal of this turn
2. Files changed
3. Key implementation notes
4. Commands to run
5. Validation / acceptance steps
6. Risks / deferred work

If database schema changed, also include:

7. Migration notes
8. Rollback plan

---

## Testing and validation rules

Always validate changed code.

Minimum expectations:

- run relevant unit tests
- run lint / format checks for touched files
- run the narrowest realistic integration path when possible

Do not claim a command passed unless you actually ran it.

If you could not run a check, say exactly why.

---

## Documentation update rules

When behavior, API, schema, task flow, or operator workflow changes, update the matching docs in the same turn if the phase allows it.

At minimum, keep these in sync when relevant:

- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `PLANS.md`

Do not leave architecture-changing code undocumented.

---

## Database and migration rules

- Schema changes must be introduced through migrations first.
- Prefer explicit constraints and indexes.
- Preserve backward-safe semantics when possible.
- Never hand-wave ledger entities; if provenance is required, model it explicitly.
- Do not collapse distinct lifecycle objects into a single “misc JSON” table unless the spec explicitly allows it.

---

## Workflow and state machine rules

For any task-state work:

- model state transitions explicitly
- emit task events on meaningful transitions
- preserve checkpoint / resume semantics
- distinguish transient failure from terminal failure
- do not invent hidden states without documenting them

---

## Acquisition and evidence rules

For search / crawl / parse / index work:

- canonicalize URLs before treating them as stable identities
- record attempts, not just final outcomes
- persist evidence artifacts in a traceable way
- never generate unsupported claims just to make the output look complete

For claim work:

- do not output a “supported” claim without evidence
- surface contradiction when present
- mark uncertainty explicitly

---

## Security and operational rules

- never commit secrets
- use environment variables for credentials and endpoints
- default to least privilege
- keep network and file access as tight as practical
- call out any SSRF, prompt injection, unsafe file parsing, or unsafe shelling risk you notice

If a change weakens a security boundary, state it explicitly.

---

## Dependency rules

- prefer existing project dependencies
- do not add a new production dependency without explaining why
- pin versions in project files
- avoid framework churn unless required by the current phase

---

## Review posture

Be conservative.

Flag:
- correctness risk
- schema drift
- broken phase boundaries
- missing tests
- missing docs
- unverifiable claims
- silent behavior changes

Do not optimize for “more code shipped”.
Optimize for: correctness, traceability, reversibility, and clear operator understanding.

---

## When using plans

For any task expected to span multiple edits, multiple phases, or more than one focused working session:

- create or update an ExecPlan under the rules in `PLANS.md`
- keep progress current
- keep validation current
- keep deferred items explicit

Do not ask the user for “next step” if the active plan already defines the next milestone.

---

## Local conventions for this repository

Prefer these implementation priorities:

1. correctness
2. evidence traceability
3. resumability
4. observability
5. ergonomics
6. performance polish

Prefer these artifact names and concepts:

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

Keep naming aligned with the spec unless there is a compelling reason to change it.

---

## If blocked

If blocked by ambiguity:

1. state the ambiguity clearly
2. choose the narrowest safe interpretation
3. proceed if the risk is low
4. otherwise stop at the boundary and explain what remains unresolved

Do not fabricate certainty.
Do not over-design around unknowns.