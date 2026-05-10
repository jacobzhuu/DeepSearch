# Plan: LLM-Guided Iterative Research Loop

This plan upgrades DeepSearch from a mostly fixed search-and-filter pipeline into an LLM-guided iterative research loop. The goal is not to let the model freely write unsupported reports, but to let the model do what it is good at: decomposing the question, expanding search vocabulary, judging whether evidence is useful, identifying missing coverage, and deciding the next search direction.

The core change is:

> Replace hardcoded query suffixes and fragile heuristic source ordering with a bounded LLM research controller that repeatedly searches, fetches, evaluates coverage, and only synthesizes the final report after enough useful evidence has been collected.

The deterministic pipeline remains available as the fallback path. The LLM path should be introduced behind feature flags, logged in detail, and evaluated with regression tasks before becoming the default.

---

## 1. Current Pipeline and Main Bottlenecks

| Stage | Current Location | Current Behavior | Main Issue |
| :--- | :--- | :--- | :--- |
| Task entrypoint | `services/orchestrator/app/services/pipeline_worker.py` (`ResearchPipelineWorker._run_task`) | Dequeues a task and starts the pipeline runner. | No issue. |
| Main orchestration | `services/orchestrator/app/services/debug_pipeline.py` (`DebugRealPipelineRunner.run`) | Executes the fixed state machine and records events. | The loop is still stage-driven, not coverage-driven. |
| Initial planning | `services/orchestrator/app/planning/planner.py` (`ResearchPlannerService.plan`) | LLM can generate subquestions, initial queries, answer slots, and preferred domains. | Planning happens mostly once; later correction is weak. |
| Search discovery | `services/orchestrator/app/services/search_discovery.py` (`SearchDiscoveryService.discover_candidates`) | Executes planner or fallback queries through SearXNG/YaCy. | Search terms are not iteratively improved by evidence quality. |
| Source intent scoring | `services/orchestrator/app/research_quality/source_intent.py` (`classify_source_intent`) | Uses domain/path/query heuristics to infer source intent. | English-biased, brittle, and too easy to mis-rank Chinese or niche sources. |
| Source judging | `services/orchestrator/app/research_quality/source_judge.py` (`SourceJudgeService.judge_candidates`) | Optional LLM priority delta for top candidates. | Currently only adjusts scores; it does not make structured source-selection decisions. |
| Acquisition | `services/orchestrator/app/services/acquisition.py` (`AcquisitionService.acquire_candidates`) | Fetches candidates until `max_candidates_per_request` or `target_successful_snapshots`. | Good sources can remain unattempted because the fetch budget is too small and too static. |
| Claim drafting / verification | `services/orchestrator/app/services/claims.py` | LLM-assisted claim extraction and verification. | Useful, but it happens after the search/fetch bottleneck has already limited evidence quality. |
| Gap analysis | `services/orchestrator/app/research_quality/gap_analyzer.py` (`analyze_required_slot_gaps`) | Detects missing slots and appends hardcoded suffixes such as `official docs`. | This is the biggest mismatch: it is called “gap analysis”, but it is not a true research strategy. |
| Gap round | `debug_pipeline.py` (`_run_research_more_round`) | Executes another search/fetch/parse/claim cycle. | The loop exists, but the next query generation is heuristic. |
| Report synthesis | `services/orchestrator/app/services/reporting.py` | Generates the final Markdown report from verified claims. | Report quality depends heavily on whether earlier evidence is sufficient. |

### Bottleneck Summary

The current design has already introduced some LLM capability, but the most important search decisions are still controlled by static rules:

1. Initial query generation may use LLM, but later query refinement usually falls back to heuristic gap queries.
2. Source selection still relies too much on domain/path rules and small fetch budgets.
3. The system stops after a fixed number of rounds, not after a meaningful coverage condition.
4. It may generate a final report even when the evidence set is thin, repetitive, or missing key answer slots.

For a deep-research product, the correct behavior should be closer to:

> Search → read source summaries → judge coverage → ask what is still missing → generate better keywords → search again → stop only when the answer is sufficiently supported or the budget is exhausted.

---

## 2. Design Principle

The new design should follow four rules.

### 2.1 LLM Generates Search Strategy, Not Just Final Text

The LLM should be used before report writing, especially in:

- query decomposition;
- synonym and bilingual keyword expansion;
- source-type targeting;
- deciding whether a result is worth fetching;
- detecting missing answer slots;
- deciding whether another search round is needed.

The final report should still be grounded in verified claims and citation spans. The LLM may write the prose, but it must not invent evidence.

### 2.2 Heuristics Become Guardrails, Not the Main Decision Maker

Hard rules should only handle severe cases:

- invalid URL;
- unsupported MIME type;
- empty or near-empty page;
- duplicate canonical URL;
- obvious spam or fetch failure;
- safety and cost limits.

They should not be the main mechanism for deciding whether a source is intellectually useful. Relevance, authority, answer-slot coverage, and novelty should be judged by the LLM when enabled.

### 2.3 The Loop Stops by Coverage, With a Hard Budget Ceiling

The system should stop when either condition is reached:

1. Coverage is good enough: required answer slots are supported by enough diverse, reliable evidence.
2. Budget is exhausted: max rounds, max queries, max fetches, or max LLM calls has been reached.

This keeps the system intelligent without letting it run indefinitely.

### 2.4 Every LLM Decision Must Be Observable

Each LLM strategy decision should be recorded into `TaskEvent` diagnostics:

- why these queries were generated;
- which answer slots they target;
- which domains or source types are preferred or avoided;
- why the loop continues or stops;
- what budget remains;
- what confidence the system has in current coverage.

This is essential for debugging, regression tests, and user-facing transparency later.

---

## 3. Proposed Architecture

Add a new controller layer between planning, search, acquisition, and reporting.

```text
User Question
    ↓
Initial LLM Research Plan
    ↓
Research Loop Controller
    ├─ Generate / refine search queries
    ├─ Search discovery
    ├─ LLM source triage
    ├─ Fetch selected sources
    ├─ Parse / index
    ├─ Draft / verify claims
    ├─ Evaluate answer-slot coverage
    └─ Decide: continue or stop
    ↓
Grounded Report Synthesis
```

The key new component is `LLMResearchStrategist`.

### New File

`services/orchestrator/app/research_quality/llm_research_strategist.py`

### Main Responsibility

The strategist receives the current research state and returns the next action:

- continue searching;
- generate targeted queries;
- fetch more results from existing candidates;
- ask for source diversity;
- stop and synthesize;
- stop with low-confidence warning.

It should not fetch, parse, or write the report directly. It only decides the next research move.

---

## 4. Research State Passed to the LLM Strategist

The LLM should not receive the whole database. It should receive a compact structured summary.

```json
{
  "question": "什么是LLM中的token？",
  "normalized_question": "Explain what a token is in large language models.",
  "round_index": 1,
  "budget_remaining": {
    "max_rounds_remaining": 2,
    "search_queries_remaining": 6,
    "fetch_attempts_remaining": 8,
    "llm_calls_remaining": 5
  },
  "answer_slots": [
    {
      "slot_id": "definition",
      "description": "Basic definition of token in LLMs",
      "required": true,
      "coverage_status": "weak",
      "strong_claim_count": 0,
      "supporting_domains": [],
      "missing_reason": "No concise source explains token as the unit processed by the model."
    },
    {
      "slot_id": "tokenization_mechanism",
      "description": "How tokenization splits text into subword or symbol units",
      "required": true,
      "coverage_status": "missing",
      "strong_claim_count": 0,
      "supporting_domains": [],
      "missing_reason": "Current sources discuss LLMs generally but not tokenization."
    }
  ],
  "previous_queries": [
    {
      "query_text": "什么是 LLM token",
      "round": 0,
      "useful_sources_found": 1,
      "notes": "Found generic blog posts, weak authority."
    }
  ],
  "candidate_summary": [
    {
      "url": "https://example.com/article",
      "title": "LLM basics",
      "snippet": "...",
      "domain": "example.com",
      "attempt_status": "UNATTEMPTED",
      "known_coverage": []
    }
  ],
  "verified_claim_summary": [
    {
      "claim": "Large language models process text in units called tokens.",
      "support_level": "moderate",
      "domains": ["platform.openai.com"],
      "covered_slots": ["definition"]
    }
  ]
}
```

This input lets the model reason about the actual weakness of the current evidence instead of blindly appending suffixes.

---

## 5. LLM Research Strategist Output Schema

```json
{
  "decision": "continue_search",
  "decision_confidence": 0.82,
  "stop_reason": null,
  "coverage_assessment": {
    "overall_status": "insufficient",
    "required_slots_missing": ["tokenization_mechanism", "examples", "limitations"],
    "main_problem": "The current evidence defines LLMs generally but lacks a clear explanation of tokenization and examples."
  },
  "next_queries": [
    {
      "query_text": "LLM token tokenization subword example",
      "language": "en",
      "target_slots": ["definition", "tokenization_mechanism", "examples"],
      "expected_source_types": ["official_docs", "technical_reference", "high_quality_tutorial"],
      "rationale": "Need sources explaining tokens as text units and showing how tokenization works.",
      "priority": 1
    },
    {
      "query_text": "大语言模型 token 分词 子词 示例",
      "language": "zh",
      "target_slots": ["definition", "examples"],
      "expected_source_types": ["technical_tutorial", "reference"],
      "rationale": "The user asked in Chinese; Chinese explanations may improve answer clarity and retrieval coverage.",
      "priority": 2
    },
    {
      "query_text": "site:platform.openai.com tokenization tokens language model",
      "language": "en",
      "target_slots": ["definition", "limitations"],
      "expected_source_types": ["official_docs"],
      "rationale": "Official documentation can provide authoritative definitions and practical constraints.",
      "priority": 3
    }
  ],
  "source_selection_guidance": {
    "must_fetch_source_types": ["official_docs", "technical_reference"],
    "prefer_new_domains": true,
    "avoid_domains": ["low_quality_seo_site.com"],
    "avoid_reason": "Previous result was generic and did not add answer coverage."
  },
  "minimum_evidence_to_stop": {
    "required_slots_must_be_at_least": "moderate",
    "min_distinct_domains": 3,
    "min_primary_or_reference_sources": 1,
    "allow_report_with_warning": false
  }
}
```

### Allowed `decision` Values

- `continue_search`: generate and execute new queries.
- `fetch_more_existing_candidates`: do not search yet; fetch more unattempted candidates because useful candidates already exist.
- `stop_sufficient`: evidence is sufficient; proceed to report synthesis.
- `stop_budget_exhausted`: budget is exhausted; generate a report only if enough partial evidence exists, otherwise return a low-coverage result.
- `stop_unanswerable`: the question cannot be answered reliably from retrieved sources.

---

## 6. Source Triage Upgrade

The existing `SourceJudgeService` should be upgraded from score adjustment to structured triage.

### Current Limitation

The current service returns a soft `priority_adjustment`. This is not enough because acquisition still treats all candidates as a ranked list under a small fetch budget. A high-value source can still be skipped if it is not near the top.

### Proposed Output

```json
{
  "url": "https://platform.openai.com/docs/concepts/tokens",
  "topic_fit": "high",
  "authority": "high",
  "novelty": "high",
  "expected_covered_slots": ["definition", "tokenization_mechanism", "limitations"],
  "source_role": "primary_reference",
  "triage_decision": "must_fetch",
  "fetch_priority": 1,
  "risk_flags": [],
  "reason": "Official documentation likely covers token definition and token-related constraints."
}
```

### Allowed `triage_decision` Values

- `must_fetch`: fetch unless the source is invalid or the global hard budget is exhausted.
- `fetch_if_budget_allows`: useful but not critical.
- `defer`: keep for later if coverage remains weak.
- `skip_duplicate`: near-duplicate of already fetched source.
- `skip_low_value`: weak relevance, weak authority, or SEO-style content.
- `skip_unsafe_or_invalid`: obvious invalid, unsafe, or unsupported source.

### Acquisition Behavior

Update `AcquisitionService.acquire_candidates` so the fetch budget is allocated by source role, not just by a single ranked list.

Recommended behavior:

1. Always attempt `must_fetch` candidates first, up to `RESEARCH_ACQUISITION_MAX_MUST_FETCH_PER_ROUND`.
2. Then fetch a diverse set of `fetch_if_budget_allows` candidates.
3. Prefer candidates that cover missing required slots.
4. Avoid fetching many candidates from the same domain unless the strategist explicitly asks for it.
5. Keep `skip_*` candidates in diagnostics, but do not attempt them.

This makes source selection coverage-aware instead of ranking-only.

---

## 7. Coverage Evaluation

A new coverage evaluator should convert verified claims into answer-slot status. The evaluator can be deterministic with optional LLM assistance.

### New / Extended File

`services/orchestrator/app/research_quality/coverage_evaluator.py`

### Slot Status

Each answer slot should have one of these states:

- `missing`: no usable claim.
- `weak`: one weak/moderate claim or low-authority source only.
- `moderate`: at least one useful claim from a relevant source.
- `strong`: multiple useful claims or one authoritative source plus supporting evidence.
- `conflicted`: relevant sources disagree.

### Stop Criteria

The report can be generated when:

```json
{
  "all_required_slots_at_least": "moderate",
  "min_strong_required_slot_ratio": 0.6,
  "min_distinct_domains": 3,
  "min_authoritative_sources": 1,
  "no_unresolved_conflicts_on_required_slots": true
}
```

The exact thresholds should be configurable in `settings.py`.

Recommended settings:

```python
RESEARCH_LOOP_ENABLED=false
RESEARCH_LOOP_MAX_ROUNDS=3
RESEARCH_LOOP_MAX_TOTAL_QUERIES=16
RESEARCH_LOOP_MAX_QUERIES_PER_ROUND=5
RESEARCH_LOOP_MAX_TOTAL_FETCH_ATTEMPTS=20
RESEARCH_LOOP_MIN_DISTINCT_DOMAINS=3
RESEARCH_LOOP_MIN_AUTHORITATIVE_SOURCES=1
RESEARCH_LOOP_REQUIRED_SLOT_MIN_STATUS="moderate"
RESEARCH_LOOP_ALLOW_LOW_COVERAGE_REPORT=true
```

---

## 8. Budget Model

The current budget is too conservative for real research tasks. Instead of one small static fetch limit, use layered budgets.

### Per-Round Budgets

- `max_queries_per_round`: 3–5.
- `max_discovered_results_per_query`: 10.
- `max_fetch_attempts_per_round`: 6–10.
- `max_must_fetch_per_round`: 3.

### Whole-Run Budgets

- `max_total_rounds`: 3.
- `max_total_queries`: 12–16.
- `max_total_fetch_attempts`: 18–25.
- `max_total_llm_strategy_calls`: 4.
- `max_total_source_triage_calls`: configurable; triage only top N per query group.

### Why This Matters

For a simple concept query like `什么是LLM中的token？`, fetching only 5 candidates is too tight. A healthier loop would search broadly at first, then use the LLM to narrow down and fetch a small but diverse set of high-value sources.

---

## 9. Implementation Rollout

### Phase 0 — Observability and Budget Diagnostics

Goal: make the current failure mode visible before changing behavior.

Files:

- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/services/acquisition.py`
- `services/orchestrator/app/api/schemas/research_tasks.py`
- `docs/schema.md`

Add diagnostics:

- discovered candidate count;
- attempted fetch count;
- successful snapshot count;
- selected-but-unattempted count;
- skipped-by-budget count;
- skipped-by-triage count;
- per-domain attempt distribution;
- answer-slot coverage before and after each round;
- exact stop reason.

No behavior change in this phase.

### Phase 1 — LLM Research Strategist in Shadow Mode

Goal: let the LLM propose the next research move, but keep the old heuristic execution path.

Files:

- `services/orchestrator/app/research_quality/llm_research_strategist.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/settings.py`
- `tests/unit/orchestrator/test_llm_research_strategist.py`

Behavior:

- Build the compact research-state input after each claim verification stage.
- Call `LLMResearchStrategist` when `RESEARCH_LOOP_STRATEGIST_ENABLED=true`.
- Store the strategist output in `TaskEvent` diagnostics.
- Continue executing the old `gap_analyzer.py` output.

This makes it possible to compare heuristic gap queries and LLM-generated queries on the same task.

### Phase 2 — Use LLM Queries for Follow-Up Rounds

Goal: replace hardcoded gap suffixes with LLM-generated search queries.

Behavior:

- If strategist output is valid and `decision=continue_search`, execute `next_queries`.
- If strategist output is invalid or empty, fall back to `analyze_required_slot_gaps`.
- Deduplicate queries against previous search history.
- Preserve query rationale and target slots in task events.

Important: do not delete `gap_analyzer.py`. Keep it as deterministic fallback.

### Phase 3 — Structured LLM Source Triage

Goal: select sources by expected answer contribution, not just by heuristic score.

Files:

- `services/orchestrator/app/research_quality/source_judge.py`
- optionally rename or wrap as `services/orchestrator/app/research_quality/source_triage.py`
- `services/orchestrator/app/services/acquisition.py`
- `tests/unit/orchestrator/test_source_triage.py`

Behavior:

- Extend source judge output schema to include `triage_decision`, `source_role`, `expected_covered_slots`, and `fetch_priority`.
- Apply active triage only when `LLM_SOURCE_JUDGE_ACTIVE_RERANK=true` or a new `LLM_SOURCE_TRIAGE_ACTIVE=true` flag is enabled.
- Fetch `must_fetch` candidates before generic ranked candidates.
- Preserve all triage decisions in metadata and diagnostics.

### Phase 4 — Coverage-Driven Stop Conditions

Goal: stop by evidence sufficiency instead of a fixed gap-round count alone.

Files:

- `services/orchestrator/app/research_quality/coverage_evaluator.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/services/reporting.py`

Behavior:

- After each round, evaluate required answer slots.
- If all stop criteria are satisfied, proceed to report synthesis.
- If budget is exhausted but coverage is weak, generate a low-coverage warning and avoid overconfident report language.
- If the question is under-specified or not answerable from available sources, report that limitation explicitly.

### Phase 5 — Report Synthesis Uses Coverage Contract

Goal: make the final report reflect the evidence state.

Behavior:

- Pass `coverage_summary`, `source_diversity_summary`, and `unresolved_conflicts` into `ReportSynthesisService`.
- Require the report to include caveats when required slots are weak or missing.
- Do not allow the report writer to fill missing slots from model memory.

---

## 10. Failure Case: “什么是LLM中的token？”

### Current Failure Pattern

Observed pattern:

- Search discovered many URLs.
- Only a few were fetched because `ACQUISITION_MAX_CANDIDATES_PER_REQUEST` and `ACQUISITION_TARGET_SUCCESSFUL_SNAPSHOTS` stopped acquisition early.
- High-quality sources could be left unattempted.
- Follow-up search used hardcoded suffixes rather than reasoning about missing coverage.
- Final answer risked being thin or generic.

### Expected Behavior After This Upgrade

Round 0:

- Planner generates initial bilingual queries.
- Search discovers general and technical sources.
- Source triage marks official docs / technical references / high-quality tutorials as `must_fetch` or `fetch_if_budget_allows`.

Coverage evaluation:

- `definition`: moderate.
- `tokenization_mechanism`: weak.
- `examples`: missing.
- `limitations`: missing.

Strategist output:

- Continue search.
- Generate targeted queries such as:
  - `LLM token tokenization subword example`
  - `大语言模型 token 分词 子词 示例`
  - `site:platform.openai.com tokenization tokens language model`
  - `BPE tokenizer large language model tokens explanation`

Round 1:

- Fetch authoritative and explanatory sources.
- Verify claims.
- Coverage becomes strong enough.

Stop:

- Generate final report with definition, mechanism, examples, limits, and citations.

---

## 11. Prompt Contract for LLM Research Strategist

The prompt should be strict and operational.

```text
You are the search strategist for a grounded research pipeline.
Your job is not to answer the user's question directly.
Your job is to decide whether the current evidence is sufficient and, if not, generate the next search actions.

Rules:
1. Use only the provided research state.
2. Do not invent sources or claims.
3. Prefer queries that target missing required answer slots.
4. Generate diverse query phrasings, including bilingual queries when the user question is not English.
5. Avoid repeating queries that already failed unless you can explain why the modified query is different.
6. Prefer authoritative, primary, official, academic, or high-quality reference sources when appropriate.
7. Stop only when required answer slots have sufficient evidence or when the budget is exhausted.
8. Return valid JSON only.
```

---

## 12. Testing Plan

### Unit Tests

Add or update:

- `tests/unit/orchestrator/test_llm_research_strategist.py`
  - parses valid strategist output;
  - rejects invalid decisions;
  - clamps excessive query counts;
  - deduplicates repeated queries;
  - falls back to deterministic gap analyzer on invalid JSON or provider error.

- `tests/unit/orchestrator/test_source_triage.py`
  - parses `triage_decision`;
  - validates `fetch_priority` bounds;
  - ensures `must_fetch` candidates are attempted before normal candidates;
  - ensures `skip_duplicate` and `skip_low_value` are not fetched.

- `tests/unit/orchestrator/test_coverage_evaluator.py`
  - verifies slot status calculation;
  - verifies stop criteria;
  - verifies low-coverage warning conditions.

### Integration / Regression Tests

Add benchmark tasks:

1. Chinese concept query: `什么是LLM中的token？`
2. English technical concept: `What is LangGraph and how does it work?`
3. Current-event-like technical query with official docs preference.
4. Academic survey query requiring source diversity.
5. Ambiguous query where the system should search broadly first, then narrow.

Acceptance criteria:

- LLM-enabled loop generates nontrivial follow-up queries instead of hardcoded suffixes.
- Required answer slots improve after follow-up rounds.
- The final report is not generated until coverage is sufficient or budget is explicitly exhausted.
- When LLM loop is disabled, deterministic fallback behavior remains stable.
- Diagnostics explain why the loop stopped.

### Suggested Command Set

```bash
pytest \
  tests/unit/orchestrator/test_llm_research_strategist.py \
  tests/unit/orchestrator/test_source_triage.py \
  tests/unit/orchestrator/test_coverage_evaluator.py \
  tests/unit/orchestrator/test_report_synthesis_service.py \
  services/orchestrator/tests/test_research_tasks_api.py -q

python -m ruff check \
  services/orchestrator/app/research_quality/llm_research_strategist.py \
  services/orchestrator/app/research_quality/source_judge.py \
  services/orchestrator/app/research_quality/coverage_evaluator.py \
  services/orchestrator/app/services/debug_pipeline.py \
  services/orchestrator/app/services/acquisition.py \
  tests/unit/orchestrator/test_llm_research_strategist.py \
  tests/unit/orchestrator/test_source_triage.py \
  tests/unit/orchestrator/test_coverage_evaluator.py
```

---

## 13. Recommended Codex Prompt

```text
Goal of this turn
Upgrade the existing LLM-assisted research loop plan into an implementation-ready, coverage-driven LLM research loop. The main objective is to reduce brittle hardcoded keyword/gap heuristics and let the LLM iteratively generate search strategy, source triage, and stop decisions while preserving deterministic fallbacks and strict budget controls.

Context
The current DeepSearch pipeline already has optional LLM planning, source judging, claim drafting/verification, and report synthesis. However, follow-up query generation is still mostly heuristic (`gap_analyzer.py` appends fixed suffixes), source selection still relies too much on domain/path scoring, and acquisition can stop too early due to static fetch limits. This causes failures on simple concept queries such as `什么是LLM中的token？`, where many URLs are discovered but too few useful sources are fetched before report generation.

Implementation requirements
1. Add an LLM research strategist service, preferably in `services/orchestrator/app/research_quality/llm_research_strategist.py`.
   - It should accept a compact research-state summary: user question, normalized question, previous queries, budget remaining, candidate summary, verified claim summary, and answer-slot coverage summary.
   - It should return strict JSON with: `decision`, `decision_confidence`, `coverage_assessment`, `next_queries`, `source_selection_guidance`, and `minimum_evidence_to_stop`.
   - Allowed decisions: `continue_search`, `fetch_more_existing_candidates`, `stop_sufficient`, `stop_budget_exhausted`, `stop_unanswerable`.
   - It must not answer the user directly or invent claims/sources.

2. Integrate the strategist into the existing loop in `services/orchestrator/app/services/debug_pipeline.py`.
   - First implement shadow mode: record strategist output in `TaskEvent` diagnostics without changing execution.
   - Then, behind a feature flag, use strategist-generated queries for follow-up rounds instead of the hardcoded output from `gap_analyzer.py`.
   - Keep `gap_analyzer.py` as deterministic fallback when LLM is disabled, fails, or returns invalid JSON.

3. Upgrade source judging into structured source triage.
   - Extend `SourceJudgeService` or wrap it with a new source triage service.
   - Output fields should include: `topic_fit`, `authority`, `novelty`, `expected_covered_slots`, `source_role`, `triage_decision`, `fetch_priority`, `risk_flags`, and `reason`.
   - Allowed triage decisions: `must_fetch`, `fetch_if_budget_allows`, `defer`, `skip_duplicate`, `skip_low_value`, `skip_unsafe_or_invalid`.
   - Update `AcquisitionService` so `must_fetch` candidates are attempted first within a bounded must-fetch budget.

4. Add or extend a coverage evaluator.
   - Convert verified claims into per-slot status: `missing`, `weak`, `moderate`, `strong`, or `conflicted`.
   - Stop report generation only when required slots meet the configured minimum status, or when budget is exhausted with an explicit low-coverage warning.

5. Add settings and diagnostics.
   - Add feature flags and budgets in `settings.py`, such as:
     - `RESEARCH_LOOP_ENABLED`
     - `RESEARCH_LOOP_STRATEGIST_ENABLED`
     - `RESEARCH_LOOP_STRATEGIST_SHADOW_MODE`
     - `RESEARCH_LOOP_MAX_ROUNDS`
     - `RESEARCH_LOOP_MAX_TOTAL_QUERIES`
     - `RESEARCH_LOOP_MAX_QUERIES_PER_ROUND`
     - `RESEARCH_LOOP_MAX_TOTAL_FETCH_ATTEMPTS`
     - `RESEARCH_LOOP_MIN_DISTINCT_DOMAINS`
     - `RESEARCH_LOOP_MIN_AUTHORITATIVE_SOURCES`
     - `RESEARCH_LOOP_REQUIRED_SLOT_MIN_STATUS`
   - Record diagnostics for each round: generated queries, query rationales, targeted slots, source triage decisions, selected-but-unattempted count, skipped-by-budget count, coverage before/after, and stop reason.

6. Tests
   - Add unit tests for strategist JSON parsing, invalid output fallback, query deduplication, source triage decision parsing, acquisition ordering, and coverage stop criteria.
   - Add or update integration/regression tests for `什么是LLM中的token？` to verify that LLM-enabled follow-up generates targeted bilingual/technical queries and improves answer-slot coverage.
   - Ensure deterministic fallback behavior remains stable when all LLM loop flags are disabled.

Non-goals
- Do not remove deterministic fallback.
- Do not let the LLM generate uncited claims.
- Do not remove existing planner/source judge/reporting services unless a small wrapper is cleaner.
- Do not make the loop unbounded; all LLM-driven behavior must be budgeted and observable.

Expected outcome
The pipeline should behave like an iterative research agent: it searches, evaluates coverage, decides what is missing, generates better keywords, fetches more useful sources, and only then generates a grounded report. Hard rules should remain for severe invalid cases and budget/safety control, but they should no longer be the primary mechanism for query expansion or source usefulness judgment.
```

---

## 14. Implementation Log

### 2026-05-08 / implementation pass

- Implemented `services/orchestrator/app/research_quality/llm_research_strategist.py` as a bounded
  structured LLM strategy parser/caller. It accepts compact state, validates allowed decisions, clamps
  and deduplicates generated queries, and returns diagnostics suitable for task events.
- Implemented `services/orchestrator/app/research_quality/coverage_evaluator.py` as a deterministic
  slot/source sufficiency evaluator. It is used for research-loop diagnostics and budget-exhaustion
  warnings without adding schema.
- Integrated strategist diagnostics into `DebugRealPipelineRunner` after verification/gap evaluation.
  Default behavior remains shadow-only. When `RESEARCH_LOOP_ENABLED=true`,
  `RESEARCH_LOOP_STRATEGIST_ENABLED=true`, and `RESEARCH_LOOP_STRATEGIST_SHADOW_MODE=false`, valid
  `continue_search` output can replace deterministic `gap_analyzer.py` supplemental queries.
- Extended `SourceJudgeService` output parsing with structured triage fields and added active-triage
  acquisition ordering. Active triage is separately gated by `LLM_SOURCE_TRIAGE_ACTIVE`; skipped or
  must-fetch decisions are visible in acquisition diagnostics.
- Added settings, `.env.example` entries, API observability field `research_strategy`, and docs updates
  in `docs/architecture.md`, `docs/api.md`, `docs/schema.md`, and `docs/runbook.md`.
- Added unit tests for strategist parsing/fallback, coverage stop criteria, and source triage ordering.

Validation performed in this pass:

- `python -m pytest tests/unit/orchestrator/test_llm_research_strategist.py tests/unit/orchestrator/test_coverage_evaluator.py tests/unit/orchestrator/test_source_triage.py tests/unit/orchestrator/test_source_judge.py -q` — passed
- `python -m pytest tests/unit/orchestrator/test_llm_research_strategist.py tests/unit/orchestrator/test_coverage_evaluator.py tests/unit/orchestrator/test_source_triage.py tests/unit/orchestrator/test_source_judge.py tests/unit/orchestrator/test_gap_analyzer.py -q` — passed
- `python -m pytest services/orchestrator/tests/test_debug_pipeline_api.py -q` — passed
- `python -m pytest tests/unit/orchestrator/test_llm_settings_and_providers.py -q` — passed
- `python -m ruff check ...` on touched Python files — passed
- `python -m black --check ...` on touched Python files — passed
- `python -m mypy ...` on the touched Python file set — failed on pre-existing/imported type
  issues in `services/orchestrator/app/research_quality/llm_assistance.py`,
  `services/orchestrator/app/reporting/markdown.py`, and
  `services/orchestrator/app/services/reporting.py`; the new debug-pipeline typing issue found
  during this pass was fixed.

Deferred:

- Full live regression for `什么是LLM中的token？` still requires a configured live LLM/search/index
  stack and should be run through `scripts/live_acceptance.py` or a dedicated profile.
- `fetch_more_existing_candidates` remains a validated strategist decision for diagnostics, but active
  execution still falls back to the deterministic existing-candidate paths unless it produces
  `continue_search` queries.

## 15. Final Target State

After this implementation, DeepSearch should no longer behave like:

```text
Generate several queries → fetch a few top URLs → append fixed suffixes if weak → generate report.
```

It should behave like:

```text
Plan → search → triage sources → fetch → verify claims → evaluate coverage → ask what is missing → generate better queries → repeat until sufficient → synthesize grounded report.
```

That is the core difference between a rule-heavy search pipeline and a real LLM-assisted deep research loop.
