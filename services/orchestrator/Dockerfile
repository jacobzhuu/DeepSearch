FROM python:3.11.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./pyproject.toml
COPY alembic.ini ./alembic.ini
COPY migrations ./migrations
COPY packages ./packages
COPY services ./services

RUN python -m pip install --upgrade pip==25.0.1 \
    && python -m pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "services.orchestrator.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
