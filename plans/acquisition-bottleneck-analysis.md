# Acquisition bottleneck — task 655f207e-c678-4137-8c5a-c80623682c72 (snapshot)

Data source: `data/dev.db` (see `DATABASE_URL` in `.env`).

## Ledger counts (SQLite)

- `candidate_url` rows for task: **84**
- `fetch_job` rows for task: **18** (all `HTTP` mode in this snapshot)
- Successful HTTP fetches (`fetch_job.status=SUCCEEDED` with latest attempt `error_code` null): **16**
- Failed with `body_too_large`: **2** (`docs.langchain.com`)

## Effective limits (from `.env` sample)

- `ACQUISITION_MAX_CANDIDATES_PER_REQUEST=20` — caps **new** fetch jobs per acquisition API/worker call, not total over task lifetime.
- `ACQUISITION_MAX_RESPONSE_BYTES=1048576` (1 MiB) — static HTTP stream cap; **`body_too_large` is excluded from Playwright fallback** by design.
- `RESEARCH_ACQUISITION_MAX_MUST_FETCH_PER_ROUND=3` — triage prioritization, not the 18 vs 84 gap by itself.

## Why 18 fetch jobs vs 84 candidates

Typical explanations (consistent with ledger):

1. **Repeated acquisition rounds** each create fetch jobs only until success targets / budgets are met; many candidates never reach a fetch round.
2. **Existing `fetch_job` per `(candidate_url_id, mode)`** — second passes skip HTTP jobs that already exist.
3. **Worker / pipeline `fetch_limit`** may be lower than 20 per stage invocation; cumulative unique fetches can stay below candidate count.
4. **Triage** (`must_fetch`, `skip_*`) shrinks the ordered list before fetch; LangGraph official-docs bias can defer low-yield URLs.

## `body_too_large` on docs.langchain.com

Large HTML/CSS assets exceed `ACQUISITION_MAX_RESPONSE_BYTES`. **Browser fallback does not apply** (`excluded_body_too_large`). Mitigations are product choices: raise cap (risk), domain-aware limits, truncation with explicit ledger flags, or alternate acquisition (e.g. sitemap / text-only endpoints) — **not** implemented in this turn.

## Browser fallback rows

- **`BROWSER_RENDERED` count for task: 0** — expected given no eligible static outcomes (`body_too_large` excluded; other fetches succeeded without SPA soft-hold).

## Follow-up (funnel metrics + batch events)

- Each `acquire_candidates` completion may emit `acquisition.fetch_batch_summary` (when `TaskEventRepository` is wired) with `stop_reason`, `unattempted_candidate_ids`, and caps — use `GET .../acquisition/funnel-metrics` for `candidate_not_fetched_reason_distribution`, `fetch_batch_stop_reason_distribution`, `browser_fallback_task_metrics`, and `body_too_large` breakdowns.
- Operator benchmark helper: `scripts/benchmark_acquisition_yield.py` (`DATABASE_URL`, `TASK_IDS`).
