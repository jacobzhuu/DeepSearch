# API

## Phase 0 endpoints

Only system health endpoints are implemented in this phase.

### `GET /healthz`

Purpose: process liveness for the minimal FastAPI service.

Response `200 OK`:

```json
{
  "status": "ok"
}
```

### `GET /readyz`

Purpose: basic readiness for the Phase 0 service shell.

Response `200 OK`:

```json
{
  "environment": "development",
  "service": "deepresearch-orchestrator",
  "status": "ready"
}
```

## Out of scope in Phase 0

The research task API surface described in the product spec is intentionally not implemented yet.
