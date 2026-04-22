# Phase 2

## Goal

Expose the first thin research task API and task event stream, backed only by database state transitions.

## Deliverables

- `POST /api/v1/research/tasks`
- `GET /api/v1/research/tasks/{task_id}`
- `GET /api/v1/research/tasks/{task_id}/events`
- `POST /api/v1/research/tasks/{task_id}/pause`
- `POST /api/v1/research/tasks/{task_id}/resume`
- `POST /api/v1/research/tasks/{task_id}/cancel`
- `POST /api/v1/research/tasks/{task_id}/revise`
- task service layer built on the existing repositories
- stable task event types and payload structure
- API, service, and repository tests

## Explicitly excluded

- workers, queues, and LangGraph execution
- search, fetch, parse, index, claim, verification, and report logic
- claims and report HTTP endpoints
