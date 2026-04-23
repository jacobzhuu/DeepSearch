# Phase 2

## Goal

Expose the first thin research task API and task event stream, backed only by database state transitions, and harden the contract for the next runtime phase.

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
- additive `revision_no` on `research_task`
- additive per-task `task_event.sequence_no`
- `/events` polling support via ordered `sequence_no`, `after_sequence_no`, and `limit`
- reserved future runtime statuses documented and modeled without making them writable yet
- API, service, and repository tests

## Explicitly excluded

- workers, queues, and LangGraph execution
- transitions into `QUEUED`, `RUNNING`, `FAILED`, `COMPLETED`, or `NEEDS_REVISION`
- search, fetch, parse, index, claim, verification, and report logic
- claims and report HTTP endpoints
