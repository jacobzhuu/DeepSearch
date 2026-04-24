PYTHON ?= python3
APP_MODULE = services.orchestrator.app.main:app
ALEMBIC = $(PYTHON) -m alembic -c alembic.ini
COMPOSE ?= docker compose --env-file .env.compose
COMPOSE_DEV ?= $(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml

.PHONY: install lint format test run precommit-install db-upgrade db-downgrade compose-up compose-down compose-dev-up compose-dev-down init-buckets init-index smoke-test migrate

install:
	$(PYTHON) -m pip install --upgrade pip==25.0.1
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m black --check .
	$(PYTHON) -m mypy packages/db services/orchestrator/app services/orchestrator/tests tests/unit

format:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m black .

test:
	$(PYTHON) -m pytest

run:
	$(PYTHON) -m uvicorn $(APP_MODULE) --host $${APP_HOST:-127.0.0.1} --port $${APP_PORT:-8000} --reload

precommit-install:
	$(PYTHON) -m pre_commit install

db-upgrade:
	$(ALEMBIC) upgrade head

db-downgrade:
	$(ALEMBIC) downgrade base

migrate:
	./scripts/migrate.sh upgrade head

init-buckets:
	$(PYTHON) scripts/init_buckets.py

init-index:
	$(PYTHON) scripts/init_index.py

smoke-test:
	$(PYTHON) scripts/smoke_test.py

compose-up:
	$(COMPOSE) up -d

compose-down:
	$(COMPOSE) down --remove-orphans

compose-dev-up:
	$(COMPOSE_DEV) up -d

compose-dev-down:
	$(COMPOSE_DEV) down --remove-orphans
