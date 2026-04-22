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

The repository defaults to a local SQLite database for Phase 1 validation:

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
DATABASE_URL=sqlite:////tmp/deepresearch_phase1.db python3 -m alembic -c alembic.ini upgrade head
DATABASE_URL=sqlite:////tmp/deepresearch_phase1.db python3 -m alembic -c alembic.ini downgrade base
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

The dev compose stack currently starts only the orchestrator service. Phase 0 deliberately excludes databases and other backing services.
The dev compose stack currently starts only the orchestrator service. Phase 1 adds the persistence code and migrations, but backing database services are still intentionally deferred.
## Phase 1 scope reminder

The persistence layer exists, but no public research task API, worker behavior, search, fetch, parse, or reporting logic has been implemented yet.
