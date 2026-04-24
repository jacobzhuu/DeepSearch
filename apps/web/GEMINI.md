1. # DeepSearch Web Frontend

   ## Role of this directory

   This directory contains the standalone web frontend for the DeepSearch project.

   The backend foundation is already complete enough for frontend integration.
   The frontend must consume the existing FastAPI backend and should not invent new backend behavior.

   Work only inside `apps/web` unless a very small backend integration bug clearly blocks frontend development.

   ---

   ## Current project reality

   DeepSearch is a self-hosted research platform with this backend flow already implemented:

   - task
   - search
   - fetch
   - parse
   - index
   - draft
   - verify
   - report

   The current repository route is:

   - host-local / self-hosted Linux is the main path
   - Docker / compose is optional tooling, not the primary requirement
   - backend product capabilities are frozen except for bugfixes and frontend integration fixes
   - the current priority is frontend development and frontend-backend integration

   Do not shift the project back to a Docker-first or deployment-first direction.

   ---

   ## Frontend scope

   Build a first-version frontend web app that helps a single operator use the existing DeepSearch backend.

   This frontend is not a marketing site, not an SSR app, and not a generic dashboard framework.

   It should provide a clean operator UI for the current backend workflow.

   ---

   ## Required frontend stack

   Use this stack unless there is a very strong reason not to:

   - Vite
   - React
   - TypeScript
   - React Router

   Preferred additions:

   - a lightweight API client layer
   - a lightweight data fetching/cache layer
   - markdown rendering for report display

   Do not introduce unnecessary complexity.

   Avoid:
   - Next.js
   - SSR
   - auth frameworks
   - heavyweight state management unless clearly necessary
   - UI over-engineering

   ---

   ## Non-goals

   Do not implement or assume any of the following in the frontend:

   - login / auth
   - OpenClaw integration
   - HTML/PDF export
   - planner / gap analyzer UI
   - advanced admin console
   - backend feature expansion
   - undocumented backend endpoints
   - speculative UI for features that do not exist yet

   ---

   ## Backend integration rules

   The frontend must treat the existing backend as the source of truth.

   ### API usage rules

   - API base URL must come from `VITE_API_BASE_URL`
   - do not hardcode backend URLs into application logic
   - do not invent endpoints
   - do not infer hidden backend fields as stable contract
   - prefer documented response fields only
   - if a field looks implementation-specific, do not make the UI depend on it unless clearly documented

   ### Main API areas expected

   The frontend may need to consume these existing backend areas:

   - research tasks
   - search discovery
   - fetch/acquisition
   - parsing / source documents / source chunks
   - indexing / retrieval
   - claims / claim evidence / verification
   - report artifact
   - health / ready / metrics for debugging only

   ### Contract discipline

   If an API contract appears unclear:

   1. check repository docs first
   2. prefer conservative assumptions
   3. document the assumption clearly in code comments or README
   4. avoid changing backend behavior unless absolutely necessary

   ---

   ## First-version pages

   Build only these first-version routes unless explicitly told otherwise:

   1. `/tasks/new`
      - create a research task

   2. `/tasks/:taskId`
      - view task summary and navigation entry points

   3. `/tasks/:taskId/sources`
      - inspect source documents and source chunks

   4. `/tasks/:taskId/claims`
      - inspect drafted claims, evidence, and verification state

   5. `/tasks/:taskId/report`
      - view the generated markdown report

   A simple home redirect or landing page is acceptable, but do not expand scope beyond the routes above.

   ---

   ## Page responsibilities

   ### Task create page
   Should allow:
   - entering query
   - submitting task creation
   - redirecting to task detail

   ### Task detail page
   Should show:
   - task metadata
   - current status
   - revision info if available
   - links to sources / claims / report

   ### Sources page
   Should show:
   - source documents
   - source chunks
   - enough provenance to help debugging and review

   ### Claims page
   Should show:
   - claims
   - verification status
   - support / contradict evidence summaries
   - rationale if available

   ### Report page
   Should show:
   - markdown artifact content
   - report metadata
   - graceful handling when no report exists yet

   ---

   ## Project structure expectations

   Organize the frontend with clear boundaries.

   Preferred shape:

   - `src/app`
   - `src/pages`
   - `src/features`
   - `src/components`
   - `src/lib`
   - `src/types`
   - `src/styles`

   Use feature-oriented grouping where it improves clarity.

   Suggested feature domains:
   - tasks
   - sources
   - claims
   - report
   - health/debug

   Do not build a giant unstructured `components/` folder.

   ---

   ## Development priorities

   Prioritize in this order:

   1. correctness of backend integration
   2. stable routing and page structure
   3. type-safe API consumption
   4. clear error/loading states
   5. simple usable UI
   6. visual polish

   The first version should be clean and reliable, not flashy.

   ---

   ## Styling guidance

   Keep styling simple and maintainable.

   Allowed approaches:
   - plain CSS
   - CSS modules
   - a lightweight utility approach if justified

   Do not spend early time on design systems or animation-heavy polish.

   The first goal is a usable operator UI.

   ---

   ## Error handling expectations

   The frontend must handle these states cleanly:

   - backend unavailable
   - task not found
   - empty sources
   - empty claims
   - empty report
   - verification still in draft/mixed/unsupported states
   - malformed or unexpected backend payloads

   Do not hide uncertainty.
   Do not present unsupported or mixed results as if they were fully confirmed.

   ---

   ## Data and state expectations

   Prefer simple state management.

   A lightweight query/cache layer is acceptable.
   Do not introduce a global state framework unless clearly necessary.

   For the first version:
   - keep state local where possible
   - isolate API calls per feature
   - centralize only shared infrastructure like API client, env parsing, and query client

   ---

   ## Report rendering expectations

   The report page should render the existing Markdown artifact from the backend.

   Do not:
   - regenerate report content on the client
   - reinterpret unsupported claims as supported
   - invent HTML/PDF export

   Treat the backend-generated Markdown as the report source of truth.

   ---

   ## Backend freeze rule

   The backend is considered feature-frozen for now.

   Allowed backend changes only if absolutely needed for frontend integration:
   - CORS/config fixes
   - response formatting fixes
   - small contract clarifications
   - obvious bugfixes

   Not allowed:
   - new backend product features
   - new workflow stages
   - new planner/verifier capabilities
   - new report capabilities
   - new deployment goals

   ---

   ## What to read before coding

   Before doing meaningful work, review these files from the repository root as needed:

   - `../../docs/architecture.md`
   - `../../docs/api.md`
   - `../../docs/runbook.md`
   - `../../docs/schema.md`

   If needed, also inspect:
   - `../../AGENTS.md`

   Use these as grounding material.
   Do not over-read unrelated backend internals unless required.

   ---

   ## How to work with Gemini in this directory

   When starting a new task in this frontend directory:

   1. read this `GEMINI.md`
   2. inspect only the docs needed for the current page or feature
   3. propose a small implementation plan
   4. implement in small, reviewable steps
   5. keep changes local to `apps/web` whenever possible

   When uncertain, prefer a smaller and more maintainable implementation.

   ---

   ## First implementation target

   The recommended first milestone is:

   1. scaffold the app with Vite + React + TypeScript
   2. add routing
   3. add API client + environment handling
   4. create page shells for:
      - `/tasks/new`
      - `/tasks/:taskId`
      - `/tasks/:taskId/sources`
      - `/tasks/:taskId/claims`
      - `/tasks/:taskId/report`
   5. add a README with local startup instructions

   After that, connect pages incrementally:
   - task creation
   - task detail
   - report
   - claims
   - sources

   ---

   ## Definition of success for this directory

   This frontend is successful when:

   - it runs locally with `VITE_API_BASE_URL` pointed at the existing backend
   - a developer can navigate the five core routes
   - the UI can create a task and inspect task outputs
   - the report page can render backend markdown
   - the app remains small, understandable, and aligned with the current backend reality
