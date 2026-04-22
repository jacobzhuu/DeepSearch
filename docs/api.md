# API

## Phase 1 endpoints

Only system health endpoints are implemented in this phase. Phase 1 adds the persistence layer behind the scenes, but it intentionally does not expose new research task APIs yet.

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

## Out of scope in Phase 1

The research task API surface described in the product spec is still intentionally not implemented yet.
