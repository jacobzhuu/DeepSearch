# Phase 11 Deployment Packaging And Smoke Validation

## 1. Objective

Close out the current Phase 10 v1 candidate around the primary host-local / self-hosted Linux path by adding and tightening deployment helpers, initialization scripts, operator documentation, and a minimum end-to-end smoke test that exercises the existing task, search, fetch, parse, index, draft, verify, and report flow without changing product API semantics. Docker and compose may remain as optional deployment packaging, but they are not the main success criterion.

## 2. Why this exists

Phase 10 proved the individual runtime seams against real PostgreSQL, MinIO, and OpenSearch processes, but the repository still lacked a clearer operator path for starting those dependencies, bootstrapping them, and verifying the whole chain. Phase 11 closes that gap primarily for the repository owner on a self-hosted Linux path. Compose artifacts may stay in the repo, but they are no longer the principal delivery target.

## 3. Scope

### In scope

- add a base `docker-compose.yml` for:
  - PostgreSQL
  - MinIO
  - OpenSearch
  - orchestrator
- keep optional compose services for:
  - SearXNG
  - Tika
- add a dev override with simpler local defaults
- add the minimum deployment config seam required for OpenSearch security-aware production wiring without changing API semantics
- add bootstrap scripts for:
  - database migration
  - bucket initialization
  - index initialization
  - smoke test
- add a minimum smoke path covering:
  - task
  - search
  - fetch
  - parse
  - index
  - draft
  - verify
  - report
- update runbook and phase docs
- keep host-local / self-hosted Linux as the primary documented operator path

### Out of scope

- OpenClaw integration
- HTML or PDF export
- planner or gap-analyzer behavior
- new verifier semantics
- new search, fetch, parse, or retrieval capabilities
- dashboards, tracing, or broader platform automation
- multi-node clustering beyond the current single-node self-hosted target
- treating Docker or compose as the required acceptance gate

## 4. Constraints

- stay strictly within Phase 11
- do not change main API semantics
- keep the deployment package reversible and explicit
- preserve the existing filesystem backend and Phase 10 runtime paths
- distinguish dev simplifications from prod-like security boundaries, especially for OpenSearch
- do not silently widen app runtime behavior to future phases
- keep host-local / self-hosted Linux as the primary closeout path
- do not treat “someone else can directly reproduce deployment” as the current objective

## 5. Relevant files and systems

- `docker-compose.yml`
- `docker-compose.dev.yml`
- `.env.example`
- `.env.compose.example`
- `Makefile`
- `dev.sh`
- `services/orchestrator/Dockerfile`
- `services/orchestrator/app/settings.py`
- `services/orchestrator/app/indexing/backends.py`
- `services/orchestrator/app/main.py`
- `scripts/`
- `infra/opensearch/`
- `infra/minio/`
- `infra/searxng/`
- `docs/api.md`
- `docs/schema.md`
- `docs/runbook.md`
- `docs/architecture.md`
- `docs/phases/phase-11.md`

## 6. Milestones

### Milestone 1
- intent: add the minimum runtime config seam needed for dev and prod separation
- code changes:
  - OpenSearch auth and TLS env/config support
  - indexing backend updates and tests
  - `.env.example` updates
- validation:
  - targeted backend tests
  - full lint, type, and pytest after the change

### Milestone 2
- intent: add optional deployment packaging and explicit initialization artifacts
- code changes:
  - base compose
  - dev override
  - optional SearXNG and Tika profiles
  - migration, bucket-init, and index-init scripts
  - container image copy path updates for scripts
- validation:
  - config parsing checks
  - narrow script validation against host-local services where available

### Milestone 3
- intent: add the end-to-end smoke path and operator documentation
- code changes:
  - smoke test script
  - runbook and phase docs
  - optional Make targets for deployment flows
- validation:
  - smoke flow against the narrowest realistic local stack
  - explicit documentation of any unvalidated compose behavior

### Milestone 4
- intent: harden the owner-operated host-local restart path
- code changes:
  - make `dev.sh` repeatable and safe around PID files, process groups, port checks, and
    `.env` loading
  - add diagnostics for status, doctor, logs, init, and smoke commands
  - document the managed helper as the preferred local restart path
- validation:
  - shell syntax checks
  - helper command checks
  - isolated temporary-port start/stop validation where possible

## 7. Implementation log

- 2026-04-24 research:
  - reread `deep_research_codex_dev_spec.md`, `PLANS.md`, `code_review.md`, and current docs before touching Phase 11
  - confirmed the existing repository only has a Phase 0-style `docker-compose.dev.yml` and no clear host-local closeout runbook yet
  - confirmed current host still lacks Docker, so Phase 11 compose validation must rely on config inspection plus host-local process checks where possible
- 2026-04-24 milestone 1:
  - added OpenSearch auth and TLS settings to the app config surface
  - updated the OpenSearch backend builder and runtime wiring to carry username, password, TLS verify mode, and CA bundle path
  - kept the backend constructor backward compatible for existing tests by providing safe defaults
  - next: add compose packaging and initialization scripts
- 2026-04-24 milestone 2:
  - added `docker-compose.yml` as the prod-like single-node stack and repurposed `docker-compose.dev.yml` as a dev override
  - added `.env.compose.example`
  - updated the orchestrator Dockerfile to copy `scripts/`
  - added `scripts/migrate.sh`, `scripts/init_buckets.py`, `scripts/init_index.py`, `scripts/mock_searxng.py`, and `scripts/smoke_test.py`
  - added Make targets for deploy and smoke helpers
  - next: update operator docs and run host-local validation
- 2026-04-24 milestone 3:
  - rewrote `docs/runbook.md` around Phase 11 deployment order, env vars, health checks, and troubleshooting
  - updated architecture, API, schema, and phase docs to reflect “no new API semantics, deployment packaging only”
  - host-local real-process validation exposed two deployment-time defects:
    - `scripts/smoke_test.py` was inheriting proxy env vars; fixed by setting `trust_env=False`
    - `fetch` and `parse` batch logs used reserved `LogRecord` field names like `created`; fixed by renaming them to non-reserved keys
  - host-local smoke now passes end to end against real PostgreSQL, real MinIO, real OpenSearch, and the deterministic mock SearXNG helper
- 2026-04-24 route change:
  - repository goal shifted to a single-operator, host-local / self-hosted Linux path
  - compose artifacts remain optional tooling, not the primary definition of success for this plan
  - future closeout work should prefer docs, scripts, and operator recovery clarity over wider packaging scope
- 2026-04-29 milestone 4:
  - started hardening the root `dev.sh` helper as the preferred one-command host-local
    backend/frontend restart path
  - replaced shell-evaluated `.env` loading with a conservative parser that does not override
    already-exported variables
  - added managed process-group startup, stale/unowned PID handling, status/doctor/logs/init/smoke
    commands, optional mock SearXNG startup, and `.logs` / `.run` git ignores
  - updated the runbook and Phase 11 doc to make the helper the preferred owner-operated path
  - validation found and fixed a Vite working-directory bug in the helper and a smoke-mode
    default mismatch between `scripts/smoke_test.py` and `SEARCH_PROVIDER=smoke`
  - isolated temporary-port restart, smoke, stop, and optional mock-search management now pass
- 2026-04-29 web smoke fix:
  - changed the local ignored `.env` used on this host from `SEARCH_PROVIDER=searxng` to
    `SEARCH_PROVIDER=smoke` because `SEARXNG_BASE_URL=http://127.0.0.1:8080` was returning
    frontend HTML during browser testing
  - updated task detail behavior so a `FAILED` task offers a same-query replacement run instead
    of presenting a disabled in-place rerun as the primary action
  - added a task-detail diagnostic panel for `searxng_html_response` and precondition failures
  - restarted the host-local backend/frontend with `./dev.sh restart`
- 2026-05-01 live self-hosted alpha smoke:
  - started real PostgreSQL 16.13, MinIO, OpenSearch 2.19.0, and SearXNG on the host-local path
  - ran the product `/run` worker smoke against real search, MinIO storage, and OpenSearch indexing;
    the task completed with persisted source documents, chunks, claims, evidence, and a Markdown
    report artifact
  - active pause validation exposed stale SQLAlchemy task-state caching in the pipeline runner:
    `/pause` returned `PAUSED` during `SEARCHING`, but the worker advanced to `ACQUIRING`
  - fixed the boundary check to refresh the task row before continuing, added regression coverage,
    and documented the troubleshooting symptom
  - reran active pause/resume/cancel validation successfully: active pause held at `PAUSED`,
    resume returned `QUEUED`, active cancel held at `CANCELLED`
  - reran the real `/run` worker smoke successfully as task
    `09b6f2e4-c933-495c-a576-0f5c742ddd64`
  - full requested validation commands passed
- 2026-05-08 host-local full-mode helper hardening:
  - updated `scripts/run_full_deepsearch.sh` so dependency mode `auto` prefers host-local
    SearXNG/OpenSearch before optional Docker/compose
  - added managed host-local OpenSearch tarball startup under `/share/zhuzy/services`, a
    non-root runtime user, single-node loopback config, and explicit local-index fallback when
    OpenSearch cannot remain alive
  - hardened managed dependency startup with `setsid` and an OpenSearch post-readiness stability
    probe so a short-lived process is not mistaken for a usable index backend
  - set HTTP acquisition and OpenAI-compatible LLM calls to ignore process proxy environment
    variables by default, with explicit `ACQUISITION_TRUST_ENV_PROXY` and `LLM_TRUST_ENV_PROXY`
    opt-ins, after the current host's SOCKS proxy env caused `httpx` to raise before fetching
    or planning
  - updated the runbook to document Docker-less full-mode commands and the explicit
    `real-search+deterministic-local+planner+report-LLM` fallback mode
  - host-local full-mode restart and real worker smoke now pass in the current server shell
- 2026-05-09 full restart entrypoint:
  - changed root `./dev.sh restart` to delegate to `scripts/run_full_deepsearch.sh restart`
    by default, making the full local profile the one-command restart path
  - preserved the previous lightweight app-process restart behavior behind
    `DEV_RESTART_PROFILE=local ./dev.sh restart` and `./dev.sh start`
  - changed the full helper's internal app-process startup call from `./dev.sh restart` to
    `./dev.sh start` so the two scripts do not recurse
  - updated the runbook to document the new default restart semantics and the local/smoke
    opt-out path
- 2026-05-09 startup import repair:
  - fixed a backend startup failure where `services.orchestrator.app.services.claims`
    imported `rewrite_claim_self_contained` from the package facade, but
    `services/orchestrator/app/claims/__init__.py` did not re-export the helper already
    implemented in `claims/drafting.py`
  - reran the full restart command successfully; backend, worker, and frontend now start
    with `real-search+opensearch+planner+report-LLM`
- 2026-05-09 source-yield metadata repair:
  - diagnosed task `306b76ed-2ea0-44d4-b642-b3dbf499cb82` failing in
    `DRAFTING_CLAIMS` with `AttributeError: 'SearchQuery' object has no attribute
    'metadata_json'`
  - fixed `debug_pipeline._build_source_yield_summary` to read search-query slot metadata
    from the actual `SearchQuery.raw_response_json` / `expansion_metadata` JSON seam, while
    still preferring candidate-level `metadata_json` when present
  - added regression coverage for strategist/search-query target-slot metadata extraction
  - restarted the full local profile so backend and worker use the repaired code

## 8. Validation

- completed:
  - `python3 -m py_compile scripts/init_buckets.py scripts/init_index.py scripts/mock_searxng.py scripts/smoke_test.py` — passed
  - `python3 -m pytest tests/unit/orchestrator/test_indexing_backend.py -q` — passed after making the backend constructor backward compatible
  - `sh ./scripts/migrate.sh history` — passed
  - `python3 -m ruff check .` — passed
  - `python3 -m black --check .` — passed
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit` — passed
  - `python3 -m pytest` — passed
  - `python3 -m alembic -c alembic.ini upgrade head` on PostgreSQL 16.13 — passed
  - `python3 scripts/init_buckets.py` against real MinIO — passed and created `snapshots` plus `reports`
  - `python3 scripts/init_index.py` against real OpenSearch 2.19.0 — passed
  - `./scripts/migrate.sh upgrade head` and `./scripts/migrate.sh current` against PostgreSQL 16.13 — passed
  - `python3 scripts/smoke_test.py --base-url http://127.0.0.1:8000` — passed against real PostgreSQL, real MinIO, real OpenSearch, and `scripts/mock_searxng.py`
  - `curl -fsS http://127.0.0.1:8000/healthz` — passed
  - `curl -fsS http://127.0.0.1:8000/readyz` — passed
  - `curl -fsS http://127.0.0.1:8000/metrics | rg 'deepresearch_http_requests_total|deepresearch_report_results_total' -m 4` — passed
  - `bash -n dev.sh` — passed
  - `./dev.sh help` — passed
  - `./dev.sh status` — passed against the existing managed 8000/5173 services
  - `DEV_ENV_FILE=/tmp/deepsearch-devsh-noenv DEV_RUN_DIR=/tmp/deepsearch-devsh-run DEV_LOG_DIR=/tmp/deepsearch-devsh-logs DEV_BACKEND_PORT=18082 DEV_FRONTEND_PORT=15182 APP_ENV=development DATABASE_URL=sqlite:////tmp/deepsearch-devsh.db SEARCH_PROVIDER=smoke INDEX_BACKEND=local SNAPSHOT_STORAGE_BACKEND=filesystem ./dev.sh doctor` — passed
  - same isolated env with `./dev.sh restart` — passed after fixing the Vite working directory; started backend on `127.0.0.1:18082` and frontend on `127.0.0.1:15182`
  - same isolated env with `./dev.sh smoke` — passed after adding smoke-provider defaults for `deepsearch-smoke.local` and claim query `smoke`
  - same isolated env with `./dev.sh stop` and `./dev.sh status` — passed; temporary backend/frontend were stopped and ports were clear
  - `DEV_START_MOCK_SEARXNG=true DEV_SKIP_BACKEND=true DEV_SKIP_FRONTEND=true DEV_RUN_INIT=false DEV_MOCK_SEARXNG_PORT=18083 ... ./dev.sh restart` — passed; optional mock search started and readiness-checked
  - same mock env with `./dev.sh stop` — passed
  - `git diff --check` — passed
  - `cd apps/web && npm run build` — passed
  - `./dev.sh restart` — passed with `SEARCH_PROVIDER=smoke`, `INDEX_BACKEND=local`, and filesystem storage
  - `./dev.sh smoke` — passed against `http://127.0.0.1:8000`
  - manual API create plus `POST /api/v1/research/tasks/<task_id>/run` for `What is SearXNG and how does it work?` — passed with `COMPLETED`, `running_mode=smoke-search+deterministic-local+no-LLM`, 3 claims, and 1 report artifact
- 2026-05-01 live self-hosted alpha smoke:
  - real dependency health checks — passed for PostgreSQL 16.13, MinIO, OpenSearch 2.19.0
    yellow health, and SearXNG JSON search results
  - `python3 scripts/smoke_planner_pipeline.py --query "What is SearXNG and how does it work?" --base-url http://127.0.0.1:8000 --wait-seconds 420` — passed after the pause-boundary fix with task `09b6f2e4-c933-495c-a576-0f5c742ddd64`, `running_mode=real-search+opensearch+no-LLM`, 2 source documents, 5 source chunks, 5 supported claims, 5 claim-evidence rows, and 1 Markdown report artifact
  - active control validation with task `5d64653c-2fd3-496d-b818-3c223960d4e9` — passed; pause during `SEARCHING` remained `PAUSED`, resume returned `QUEUED`, cancel during `ACQUIRING` ended in `CANCELLED`
  - `python3 -m pytest services/orchestrator/tests/test_debug_pipeline_api.py::test_pipeline_boundary_refreshes_external_pause_before_next_stage -q` — passed
  - `python3 -m ruff check .` — passed
  - `python3 -m black --check .` — passed
  - `python3 -m pytest` — passed, 270 tests
  - `cd apps/web && npm run build` — passed
  - `python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit` — passed
  - `git diff --check` — passed
- 2026-05-08 host-local full-mode helper validation:
  - `bash -n scripts/run_full_deepsearch.sh` — passed
  - `./scripts/run_full_deepsearch.sh doctor` — passed; reported host SearXNG/OpenSearch
    installed and Docker daemon unavailable but optional
  - `FULL_DEEPSEARCH_DEPS_MODE=host FULL_DEEPSEARCH_ALLOW_LOCAL_INDEX_FALLBACK=false ./scripts/run_full_deepsearch.sh restart` — passed; started/reused host-local SearXNG and OpenSearch, initialized `source-chunks-v1`, and started backend, worker, and frontend with `real-search+opensearch+planner+report-LLM`
  - `curl -fsS http://127.0.0.1:9200/` — passed against OpenSearch 2.19.0 after the helper exited
  - `curl -fsS http://127.0.0.1:8000/readyz` — passed
  - `curl -fsS http://127.0.0.1:8888/search?q=deepsearch\&format=json` — passed with JSON results
  - `python3 -m pytest tests/unit/orchestrator/test_acquisition_http_client.py tests/unit/orchestrator/test_llm_settings_and_providers.py -q` — passed, 24 tests
  - `python3 -m ruff check ...` for touched Python files and tests — passed
  - `python3 -m black --check ...` for touched Python files and tests — passed
  - `git diff --check -- ...` for touched files — passed
  - `python3 scripts/smoke_planner_pipeline.py --query "What is SearXNG and how does it work?" --base-url http://127.0.0.1:8000 --wait-seconds 420` — passed with task `2de5137f-6672-4c5d-897d-db4628c42513`, `running_mode=real-search+opensearch+planner+report-LLM`, `planner_status=success`, 6 source documents, 30 chunks, 6 claims, and report artifact `ce0f4e87-55aa-4937-90aa-6e4dc0b9a409`
  - `python3 -m mypy services/orchestrator/app/acquisition/http_client.py services/orchestrator/app/llm/providers.py services/orchestrator/app/llm/client.py services/orchestrator/app/settings.py services/orchestrator/app/services/pipeline_runtime.py services/orchestrator/app/api/routes/acquisition.py` — did not pass because mypy followed imports into existing unrelated errors in `research_quality/llm_assistance.py`, `reporting/markdown.py`, and `services/reporting.py`
- 2026-05-09 full restart entrypoint validation:
  - `bash -n dev.sh` — passed
  - `bash -n scripts/run_full_deepsearch.sh` — passed
  - `./dev.sh help` — passed and documents full restart plus `DEV_RESTART_PROFILE=local`
  - `./scripts/run_full_deepsearch.sh help` — passed
  - `DEV_RESTART_PROFILE=local DEV_RUN_DIR=/tmp/deepsearch-devsh-restart-run DEV_LOG_DIR=/tmp/deepsearch-devsh-restart-logs DEV_ENV_FILE=/tmp/deepsearch-devsh-restart-noenv DEV_RUN_INIT=false DEV_SKIP_BACKEND=true DEV_SKIP_WORKER=true DEV_SKIP_FRONTEND=true ./dev.sh restart` — passed without touching current managed services
  - `FULL_DEEPSEARCH_ENV_FILE=/tmp/deepsearch-full-restart-test.env FULL_DEEPSEARCH_DEPS_MODE=none LLM_API_KEY=dev-test-key DEV_RUN_DIR=/tmp/deepsearch-full-restart-run DEV_LOG_DIR=/tmp/deepsearch-full-restart-logs DEV_RUN_INIT=false DEV_SKIP_BACKEND=true DEV_SKIP_WORKER=true DEV_SKIP_FRONTEND=true ./dev.sh restart` — passed and proved `dev.sh restart` delegates to the full helper, which returns to `dev.sh start`; the temporary env file was removed afterward
  - `git diff --check -- dev.sh scripts/run_full_deepsearch.sh docs/runbook.md plans/phase-11-deployment-packaging.md` — passed
- 2026-05-09 startup import repair validation:
  - `python - <<'PY' ... from services.orchestrator.app.main import app ... PY` — passed and
    proved the backend import path no longer raises the missing `rewrite_claim_self_contained`
    import
  - `python -m pytest tests/unit/orchestrator/test_robustness_improvements.py -q` — passed,
    5 tests
  - `python -m pytest tests/unit/orchestrator/test_claim_drafting_helpers.py tests/unit/orchestrator/test_claim_drafting_service.py -q` — failed, 5 assertions; failures are existing claim-quality behavior expectations unrelated to the package export repair, while the import path itself is fixed
  - `bash -n dev.sh scripts/run_full_deepsearch.sh` — passed
  - `git diff --check -- services/orchestrator/app/claims/__init__.py dev.sh scripts/run_full_deepsearch.sh docs/runbook.md plans/phase-11-deployment-packaging.md` — passed
  - `./dev.sh restart` — passed; started backend pid `111343`, worker pid `111353`, and
    frontend pid `111358`
  - `curl -fsS http://127.0.0.1:8000/healthz` — passed
  - `curl -fsS http://127.0.0.1:8000/readyz` — passed
  - `curl -fsS http://127.0.0.1:5173` — passed
  - `./dev.sh status` — passed and showed backend/frontend listeners on `127.0.0.1:8000`
    and `127.0.0.1:5173`
- 2026-05-09 source-yield metadata repair validation:
  - `python -m pytest tests/unit/orchestrator/test_task_observability.py -q` — passed, 4
    tests
  - `python -m pytest tests/unit/orchestrator/test_pipeline_worker.py -q` — passed, 3
    tests
  - `python -m py_compile services/orchestrator/app/services/debug_pipeline.py tests/unit/orchestrator/test_task_observability.py` — passed
  - `git diff --check -- services/orchestrator/app/services/debug_pipeline.py tests/unit/orchestrator/test_task_observability.py` — passed
  - `./dev.sh restart` — passed; restarted backend pid `113096`, worker pid `113106`,
    and frontend pid `113111`
  - direct `_build_source_yield_summary` call against failed task
    `306b76ed-2ea0-44d4-b642-b3dbf499cb82` — passed and returned 42 source-yield rows
    without the previous `SearchQuery.metadata_json` exception
  - `curl -fsS http://127.0.0.1:8000/healthz` — passed
  - `curl -fsS http://127.0.0.1:8000/readyz` — passed
  - `./dev.sh status` — passed
- known host limitation:
  - the Docker CLI is installed, but the daemon was unavailable at the start of this live-smoke
    turn; compose runtime remains unvalidated here
  - this is now acceptable because compose is optional tooling rather than the primary acceptance path

## 9. Risks and unknowns

- prod-like OpenSearch security wiring may require a minimal auth/TLS seam in app config even though Phase 11 must not change API semantics
- smoke determinism depends on search-provider behavior unless a controlled search endpoint is used
- compose syntax can be written correctly yet remain unvalidated without a local compose binary
- the prod-like compose path assumes the operator supplies `infra/opensearch/certs/root-ca.pem`; this file is intentionally not generated in Phase 11
- if the project later returns to a broader reproducible-deployment target, compose runtime validation will need a dedicated follow-up milestone
- managed helper safety depends on PID files belonging to processes started by the helper; for
  older manually started processes, the status output must be inspected before manual cleanup

## 10. Rollback / recovery

- revert compose, script, deployment-config, and doc changes together
- if OpenSearch config seams are added, revert them together with compose changes to avoid leaving dead env vars behind
- remove any temporary data directories or local helper processes used for validation

## 11. Deferred work

- full production certificate management
- multi-node OpenSearch or PostgreSQL topologies
- dashboards and tracing
- OpenClaw
- HTML/PDF export
- optional Docker Compose runtime validation once a host with `docker` is available
