# Runbook

## Local setup

1. Copy `.env.example` to `.env`.
2. Use Python `3.11.11` when available. The repository includes `.python-version` for that preferred interpreter.
3. Install the development dependencies:

```bash
python3 -m pip install -e ".[dev]"
```

## Local commands

```bash
make lint
make format
make test
make run
make db-upgrade
make db-downgrade
```

If `make` is unavailable in the host environment, run the underlying commands directly:

```bash
python3 -m ruff check .
python3 -m black --check .
python3 -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit
python3 -m pytest
python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000
python3 -m alembic -c alembic.ini upgrade head
python3 -m alembic -c alembic.ini downgrade base
```

## Database migration commands

The repository defaults to a local SQLite database for Phase 2 validation:

```bash
echo "$DATABASE_URL"
```

Apply and roll back the migration:

```bash
python3 -m alembic -c alembic.ini upgrade head
python3 -m alembic -c alembic.ini downgrade base
```

For isolated validation without touching `data/dev.db`, use a temporary URL:

```bash
DATABASE_URL=sqlite:////tmp/deepresearch_phase2.db python3 -m alembic -c alembic.ini upgrade head
DATABASE_URL=sqlite:////tmp/deepresearch_phase2.db python3 -m alembic -c alembic.ini downgrade base
```

## Phase 2 API validation

Start the service:

```bash
DATABASE_URL=sqlite:////tmp/deepresearch_phase2.db python3 -m alembic -c alembic.ini upgrade head
DATABASE_URL=sqlite:////tmp/deepresearch_phase2.db python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000
```

Create a task:

```bash
curl -fsS \
  -X POST http://127.0.0.1:8000/api/v1/research/tasks \
  -H 'Content-Type: application/json' \
  -d '{"query":"近30天 NVIDIA 在开源模型生态上的关键发布与影响","constraints":{"language":"zh-CN"}}'
```

Pause, resume, revise, and cancel it:

```bash
curl -fsS -X POST http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/pause
curl -fsS -X POST http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/resume
curl -fsS -X POST http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/revise \
  -H 'Content-Type: application/json' \
  -d '{"constraints":{"max_rounds":2}}'
curl -fsS -X POST http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/cancel
curl -fsS http://127.0.0.1:8000/api/v1/research/tasks/<task_id>
curl -fsS http://127.0.0.1:8000/api/v1/research/tasks/<task_id>/events
```

## Health validation

Start the service:

```bash
python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000
```

Then validate:

```bash
curl -fsS http://127.0.0.1:8000/healthz
curl -fsS http://127.0.0.1:8000/readyz
```

## Docker compose

```bash
cp .env.example .env
docker compose -f docker-compose.dev.yml up --build
```

The dev compose stack currently starts only the orchestrator service. Phase 2 adds task APIs on top of the persistence code, but backing database services are still intentionally deferred.

## Phase 2 scope reminder

- task creation, lookup, event retrieval, pause, resume, cancel, and revise now exist
- no worker behavior, search, fetch, parse, index, verification, or reporting logic has been implemented yet
