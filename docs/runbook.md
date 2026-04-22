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
```

If `make` is unavailable in the host environment, run the underlying commands directly:

```bash
python3 -m ruff check .
python3 -m black --check .
python3 -m mypy services/orchestrator/app services/orchestrator/tests
python3 -m pytest
python3 -m uvicorn services.orchestrator.app.main:app --host 127.0.0.1 --port 8000
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
