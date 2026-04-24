# Phase 3

## Goal

Introduce the first search-discovery slice of the orchestrator: expand the task query, call a configured search provider, canonicalize and filter result URLs, and persist `search_query` plus `candidate_url` records into the ledger.

## Deliverables

- search-provider abstraction with a SearXNG-backed implementation
- minimal query expansion based on the task query and `domains_allow`
- URL canonicalization plus domain allow or deny filtering
- task-scoped candidate URL dedupe before persistence
- thin repository, service, and API read/write paths for `search_query` and `candidate_url`
- synchronous `POST /api/v1/research/tasks/{task_id}/searches`
- read APIs for persisted search queries and candidate URLs
- tests for provider parsing, canonicalization, repositories, service behavior, and API contracts
- updated API, schema, runbook, architecture, and plan documentation

## Explicitly excluded

- fetch execution, `fetch_job`, or `fetch_attempt` runtime behavior
- crawler, Playwright, Tika, or OpenSearch integration
- task queueing, worker scheduling, or LangGraph execution
- claim drafting, verification, report generation, or report APIs
- many-to-many provenance between one canonical URL and every query that surfaced it
