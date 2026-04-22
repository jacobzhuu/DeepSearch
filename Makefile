PYTHON ?= python3
APP_MODULE = services.orchestrator.app.main:app

.PHONY: install lint format test run precommit-install

install:
	$(PYTHON) -m pip install --upgrade pip==25.0.1
	$(PYTHON) -m pip install -e ".[dev]"

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m black --check .
	$(PYTHON) -m mypy services/orchestrator/app services/orchestrator/tests

format:
	$(PYTHON) -m ruff check --fix .
	$(PYTHON) -m black .

test:
	$(PYTHON) -m pytest

run:
	$(PYTHON) -m uvicorn $(APP_MODULE) --host $${APP_HOST:-127.0.0.1} --port $${APP_PORT:-8000} --reload

precommit-install:
	$(PYTHON) -m pre_commit install
