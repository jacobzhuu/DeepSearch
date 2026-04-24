# PLANS.md

This file defines how executable plans (ExecPlans) are written, maintained, and used in this repository.

An ExecPlan is a living implementation document that should be sufficient for a coding agent to resume work from the current working tree and the plan alone.

If a task is expected to require:
- multiple focused implementation turns
- architecture-sensitive changes
- schema changes
- workflow/state-machine changes
- nontrivial review or rollback planning

then an ExecPlan is required.

---

## 1. Global rules

1. Every nontrivial feature or refactor must have one active ExecPlan.
2. ExecPlans are living documents. Update them as work proceeds.
3. At any stopping point, the plan must clearly state:
   - what is already done
   - what remains
   - how to validate current progress
   - what risks or unknowns remain
4. Do not treat the plan as a static proposal.
5. If implementation diverges from the original plan, update the plan before or together with the code.
6. The plan must be understandable to a reader with no memory of prior chat context.
7. If the project goal or delivery route changes, update `AGENTS.md`, the relevant phase docs, and all active ExecPlans before continuing implementation.

---

## 2. File placement

Preferred locations:

- repository-wide / cross-cutting work:
  - `plans/<short-name>.md`
- phase-specific work:
  - `plans/phase-<n>-<short-name>.md`

Keep filenames stable once referenced by active work.

Recommended examples:

- `plans/phase-1-ledger-schema.md`
- `plans/phase-4-fetch-pipeline.md`
- `plans/reporting-artifacts-v1.md`

---

## 3. When to create a new ExecPlan

Create a new plan when at least one of these is true:

- the change spans more than one subsystem
- the change requires migrations
- the change introduces or modifies workflow states
- the task is likely to take more than one session
- the rollback story is not trivial
- the validation path is longer than one or two commands

If the task is very small and local, no ExecPlan is required.

---

## 4. Required ExecPlan structure

Every ExecPlan must contain these sections in this order.

# Title

A precise, implementation-oriented title.

## 1. Objective

What will be built or changed, in concrete engineering terms.

## 2. Why this exists

Why the change is needed.
State the architectural or product reason, not just the requested action.

## 3. Scope

Explicitly list:
- in scope
- out of scope

## 4. Constraints

List hard constraints, such as:
- phase limits
- backward compatibility requirements
- no new dependencies
- no API contract breakage
- no schema shortcuts
- security boundaries

## 5. Relevant files and systems

List the files, services, tables, APIs, or docs that matter.

## 6. Milestones

Break the work into milestones that are independently meaningful and testable.

For each milestone include:
- intent
- expected code changes
- expected validation

## 7. Implementation log

A chronological log of decisions and progress.

Each entry should include:
- date or session marker
- what changed
- why
- whether the plan changed
- what comes next

## 8. Validation

List exact commands and manual checks.

Include:
- lint / type / unit / integration checks
- expected outputs
- known unvalidated areas

## 9. Risks and unknowns

List unresolved technical risks, assumptions, and open questions.

## 10. Rollback / recovery

State how to revert or safely back out the change.

## 11. Deferred work

List intentionally postponed items.

---

## 5. Milestone rules

Milestones must be small enough to validate, but large enough to matter.

A good milestone should:
- preserve a runnable state
- have a clear acceptance check
- avoid mixing unrelated concerns

Bad milestone examples:
- “implement everything”
- “clean up later”
- “misc fixes”

Good milestone examples:
- “introduce research_task and research_run migrations”
- “persist fetch attempts and MinIO snapshot metadata”
- “generate claim drafts with citation span links only”

---

## 6. Implementation log rules

The implementation log is mandatory.

At each stop, update it with:
- what was finished
- what was partially finished
- what is next
- whether validation passed
- whether docs were updated

Do not leave the log stale.

---

## 7. Validation rules

Validation must be explicit.

Do not write:
- “tested manually”
- “works”
- “verified”

Instead write:
- exact command
- whether it passed
- what it proved
- what it did not prove

Example:

- `pytest tests/unit/test_task_repo.py -q` — passed
- `alembic upgrade head` — passed on local dev database
- `docker compose up orchestrator postgres redis` — started successfully
- manual API POST `/api/v1/research/tasks` — returned `201` with `task_id`

---

## 8. Plan maintenance rules

When implementing from an ExecPlan:

- do not ask for “next steps” if the plan already defines them
- proceed to the next milestone when safe
- keep the plan synchronized with code reality
- record deviations explicitly
- if repository-level goals changed, stop and update the governing docs and active plans first

When reviewing an ExecPlan:

- challenge scope creep
- challenge unclear validation
- challenge unstated rollback assumptions
- challenge vague milestone boundaries

---

## 9. ExecPlan template

Copy this template for new plans.

# <ExecPlan Title>

## 1. Objective

## 2. Why this exists

## 3. Scope

### In scope

### Out of scope

## 4. Constraints

## 5. Relevant files and systems

## 6. Milestones

### Milestone 1
- intent:
- code changes:
- validation:

### Milestone 2
- intent:
- code changes:
- validation:

## 7. Implementation log

- [ ] initial research completed
- [ ] milestone 1 completed
- [ ] milestone 2 completed

### Log entries
- YYYY-MM-DD / session:
  - changes:
  - rationale:
  - validation:
  - next:

## 8. Validation

## 9. Risks and unknowns

## 10. Rollback / recovery

## 11. Deferred work

---

## 10. Repository-specific guidance

For this repository, ExecPlans should pay special attention to:

- state-machine integrity
- provenance / ledger completeness
- idempotency of side effects
- migration safety
- evidence traceability
- security boundaries around network and file access
- operator-facing docs and runbook updates

If any of those are affected, the plan must mention them explicitly.
