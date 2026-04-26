# MVP Parsing Diagnostics and URL Safety Fixes

## 1. Objective

Fix the current MVP regression around parsing opacity, short HTML stability, URL safety false positives, and FAILED task run-button behavior without changing the no-LLM synchronous pipeline shape.

## 2. Why this exists

The real MVP loop produced a FAILED task at `PARSING` even though one snapshot was fetched successfully. The operator could see only `parse produced no source documents`, which was not enough to distinguish empty content, missing storage, unsupported MIME, or parser errors. At the same time, public domains were incorrectly blocked when DNS returned a mix of global and non-global IPs.

## 3. Scope

In scope:

- Keep SSRF protection enabled while allowing public domains that resolve to at least one global IP.
- Record per-snapshot parse decisions and surface them through pipeline events and task detail observability.
- Make non-empty `text/html` and `text/plain` snapshots produce source documents when a minimal safe extraction is possible.
- Disable the Task Detail run button outside `PLANNED` with an explanatory message.
- Keep backend 409 messages visible if a run request is still made.
- Update focused tests and operator docs.

Out of scope:

- LLM integration.
- Worker or queue semantics.
- LangGraph.
- PDF/Tika parsing.
- Browser fallback fetching.
- FAILED -> RUNNING rerun semantics.
- Broad workflow or schema refactors.

## 4. Constraints

- No new production dependency.
- No database schema change.
- Preserve the existing synchronous MVP pipeline.
- Preserve the SSRF boundary for loopback, private, link-local, reserved, and metadata targets.
- Keep FAILED task rerun semantics unchanged: only `PLANNED` tasks can run.

## 5. Relevant files and systems

- `services/orchestrator/app/acquisition/http_client.py`
- `services/orchestrator/app/services/parsing.py`
- `services/orchestrator/app/parsing/extractors.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/api/routes/research_tasks.py`
- `services/orchestrator/app/api/routes/parsing.py`
- `apps/web/src/pages/tasks/TaskDetailPage.tsx`
- `apps/web/src/lib/http.ts`
- `docs/api.md`
- `docs/runbook.md`

## 6. Milestones

Milestone 1: URL safety decision metadata.

- Change DNS safety evaluation to block only when all resolved IPs are non-global.
- Record `allowed_ips`, `blocked_ips`, and `decision_reason`.
- Validate with mixed public/non-global and all-private DNS tests.

Milestone 2: Parse decisions and stability.

- Add parse decisions for parsed, skipped empty, unsupported MIME, missing blob, and parser error outcomes.
- Surface decisions through parse API responses, pipeline failure details, task events, and task detail observability.
- Add short HTML title fallback.
- Validate with unit and pipeline API tests.

Milestone 3: FAILED run-button behavior.

- Disable Task Detail run button when status is not `PLANNED`.
- Display the PLANNED-only explanation.
- Format backend 409 details into a readable frontend error.

Milestone 4: Docs and validation.

- Update API and runbook notes for parse decisions and mixed DNS safety behavior.
- Run the required backend and frontend validation commands.

## 7. Implementation log

- 2026-04-26: Read required docs and located acquisition, parsing, pipeline, task detail, and frontend error paths.
- 2026-04-26: Implemented mixed-DNS URL safety handling and focused acquisition tests.
- 2026-04-26: Implemented parse decisions, short HTML title fallback, pipeline failure details, task observability, and focused parsing/pipeline tests.
- 2026-04-26: Implemented FAILED task run-button explanation and frontend 409 detail formatting.
- 2026-04-26: Narrow backend tests passed for acquisition, parsing, parsing helpers, and debug pipeline API.
- 2026-04-26: Updated API and runbook documentation for parse decisions and mixed DNS acquisition safety.
- 2026-04-26: Full backend tests, Python formatting/lint checks, and frontend production build passed.

## 8. Validation

Planned commands:

- `python3 -m pytest -q`
- `python3 -m ruff check .`
- `python3 -m black --check .`
- `cd apps/web && npm run build`

Completed narrow check:

- `python3 -m pytest tests/unit/orchestrator/test_acquisition_http_client.py tests/unit/orchestrator/test_parsing_helpers.py tests/unit/orchestrator/test_parsing_service.py services/orchestrator/tests/test_debug_pipeline_api.py -q` - passed.

Completed full checks:

- `python3 -m pytest -q` - passed.
- `python3 -m ruff check .` - passed.
- `python3 -m black --check .` - passed after formatting two touched Python files.
- `cd apps/web && npm run build` - passed.

## 9. Risks and unknowns

- A fetched page with only a title can now parse into a minimal source document. Downstream claim filtering may still reject title-only text, which is intentional and outside this parsing fix.
- Mixed DNS answers still rely on the HTTP client connecting by hostname. The change records the safety decision but does not pin the socket to a chosen allowed IP.

## 10. Rollback / recovery

- Revert this plan and the related code/test/doc changes.
- No migration rollback is required because there is no schema change.
- If the mixed-DNS behavior needs to be disabled quickly, restore the previous `blocked_ips` check that rejects any non-global DNS answer.

## 11. Deferred work

- Full FAILED task rerun semantics with run attempts, artifact isolation, or revision isolation.
- Richer parser support beyond minimal `text/html` and `text/plain`.
- Frontend unit test harness for Task Detail UI behavior; current validation is TypeScript/Vite build only.
