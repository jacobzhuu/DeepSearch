# API

## Phase 2 endpoints

Phase 2 keeps the existing system endpoints and adds the first thin research task API.

### `GET /healthz`

Purpose: process liveness for the minimal FastAPI service.

Response `200 OK`:

```json
{
  "status": "ok"
}
```

### `GET /readyz`

Purpose: basic readiness for the Phase 2 service shell.

Response `200 OK`:

```json
{
  "environment": "development",
  "service": "deepresearch-orchestrator",
  "status": "ready"
}
```

## Research task endpoints

### `POST /api/v1/research/tasks`

Purpose: create a research task in `PLANNED` status and emit `task.created`.

Request:

```json
{
  "query": "近30天 NVIDIA 在开源模型生态上的关键发布与影响",
  "constraints": {
    "domains_allow": ["nvidia.com", "github.com"],
    "language": "zh-CN"
  }
}
```

Response `201 Created`:

```json
{
  "task_id": "uuid",
  "status": "PLANNED",
  "updated_at": "2026-04-22T12:00:00Z"
}
```

### `GET /api/v1/research/tasks/{task_id}`

Purpose: return task metadata, current status, and minimal progress.

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "query": "近30天 NVIDIA 在开源模型生态上的关键发布与影响",
  "status": "PLANNED",
  "constraints": {
    "domains_allow": ["nvidia.com", "github.com"],
    "language": "zh-CN"
  },
  "created_at": "2026-04-22T12:00:00Z",
  "updated_at": "2026-04-22T12:00:00Z",
  "started_at": null,
  "ended_at": null,
  "progress": {
    "current_state": "PLANNED",
    "events_total": 1,
    "latest_event_at": "2026-04-22T12:00:00Z"
  }
}
```

### `GET /api/v1/research/tasks/{task_id}/events`

Purpose: return the ordered task event stream.

Response `200 OK`:

```json
{
  "task_id": "uuid",
  "events": [
    {
      "event_id": "uuid",
      "run_id": null,
      "event_type": "task.created",
      "payload": {
        "event_version": 1,
        "source": "api",
        "from_status": null,
        "to_status": "PLANNED",
        "changes": {
          "query": "近30天 NVIDIA 在开源模型生态上的关键发布与影响",
          "constraints": {
            "domains_allow": ["nvidia.com", "github.com"],
            "language": "zh-CN"
          }
        }
      },
      "created_at": "2026-04-22T12:00:00Z"
    }
  ]
}
```

### `POST /api/v1/research/tasks/{task_id}/pause`

Purpose: perform the minimal Phase 2 transition `PLANNED -> PAUSED` and emit `task.paused`.

### `POST /api/v1/research/tasks/{task_id}/resume`

Purpose: perform the minimal Phase 2 transition `PAUSED -> PLANNED` and emit `task.resumed`.

### `POST /api/v1/research/tasks/{task_id}/cancel`

Purpose: perform the minimal Phase 2 transition `PLANNED|PAUSED -> CANCELLED` and emit `task.cancelled`.

### `POST /api/v1/research/tasks/{task_id}/revise`

Purpose: update the persisted task query and or constraints, return the task to `PLANNED`, and emit `task.revised`.

Request:

```json
{
  "query": "聚焦 NVIDIA 与开源推理栈",
  "constraints": {
    "max_rounds": 2
  }
}
```

Command responses use the same shape:

```json
{
  "task_id": "uuid",
  "status": "PLANNED",
  "updated_at": "2026-04-22T12:05:00Z"
}
```

## Phase 2 transition rules

- `pause`: allowed only from `PLANNED`
- `resume`: allowed only from `PAUSED`
- `cancel`: allowed from `PLANNED` and `PAUSED`
- `revise`: allowed from `PLANNED` and `PAUSED`, and always results in `PLANNED`
- invalid transitions return `409 Conflict`
- unknown task ids return `404 Not Found`

## Out of scope in Phase 2

- no worker-triggered execution starts after `resume` or `revise`
- no search, fetch, parse, index, claim, or report endpoints exist yet
