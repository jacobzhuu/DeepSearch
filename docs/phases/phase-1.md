# Phase 1

## Goal

Introduce the initial research ledger schema and persistence layer without implementing task execution or business APIs.

## Deliverables

- Alembic initialization at the repository root
- first reversible migration for the required core ledger entities
- SQLAlchemy 2.x ORM models aligned with that migration
- minimal repository layer
- unit tests for migration application, round-trip persistence, and key uniqueness guarantees

## Explicitly excluded

- task orchestration or worker logic
- search, crawl, parse, index, verification, or report generation behavior
- full research task HTTP APIs
