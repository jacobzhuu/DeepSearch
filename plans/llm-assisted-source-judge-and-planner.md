# LLM-Assisted Planner and Source Judge

## 1. Objective

Add an optional LLM-assisted quality layer that improves research planning, source-quality judgment, gap diagnosis, and final prose quality while preserving deterministic grounding and auditability.

The feature will be implemented incrementally. The first implementation must be planner-only. Later milestones add a source judge in shadow mode, then active reranking, then a gap reasoner, then the existing grounded report writer path as the final optional LLM surface.

## 2. Why this exists

The deterministic no-LLM pipeline now works for real-search technical concept queries and has explicit ownership/ranking safeguards. The next quality ceiling is not basic claim generation; it is better task decomposition, better recognition of source intent, better explanations for missing answer slots, and better final wording.

LLM assistance is useful for those areas, but it is also risky:

- an LLM can over-trust mirrors, reposts, and third-party tutorials
- an LLM can invent official ownership or authority
- an LLM can produce unsupported claims if allowed into the claim path
- an LLM can make runs less reproducible if raw prompts, outputs, and guardrail decisions are not persisted

This plan treats the LLM as an advisory component. The deterministic pipeline remains the source of truth for source ownership, blocklists, fetching, parsing, evidence binding, verification, and report item grounding.

## 3. Scope

### In scope

- Define the architecture for four optional LLM components:
  - planner enhancement
  - source judge
  - gap reasoner
  - grounded report writer
- Define source judge input/output schemas, labels, confidence fields, rationale, guardrails, score-combination rules, and audit metadata.
- Define a rollout path from planner-only to shadow judging to active reranking.
- Define tests for official docs vs mirrors, upstream GitHub vs third-party repos, low-value page filtering, schema validation, and provider fallback.
- Update operator-facing architecture and runbook docs for the planned LLM-assisted quality layer.

### Out of scope

- No implementation of the source judge, gap reasoner, or new report writer behavior in this turn.
- No `.env` edits and no LLM enablement in this turn.
- No Redis, Celery, worker architecture changes, browser fetching, PDF parsing, Tika, or new production dependencies.
- No claim drafting or claim verification by LLM.
- No database migration in the planning turn. Later implementation may choose task-event/checkpoint JSON first and only add schema after the audit shape is proven.

## 4. Constraints

- `research_task` remains the primary product object.
- `/run` remains async and worker-driven.
- LLM usage must be opt-in and disabled by default.
- LLM output must be parsed through strict Pydantic schemas or equivalent structured validation.
- LLM planner output is a suggestion. Deterministic planner guardrails still preserve required queries and downrank unsafe suggestions.
- LLM source judge output is advisory unless a later milestone explicitly enables active reranking.
- The LLM cannot mark a source official without deterministic ownership evidence.
- The deterministic blocklist and low-value domain rules always win.
- The LLM cannot directly create persisted claims.
- The LLM cannot directly set `verification_status = supported`.
- Final report output must remain grounded in fetched chunks, persisted claims, `claim_evidence`, and `citation_span` rows.
- Raw secrets, API keys, and private runtime configuration must never be included in prompts, task events, or diagnostics.
- All LLM calls must have bounded input size, bounded output tokens, sanitized errors, and provider-unavailable fallback.

## 5. Relevant files and systems

- `services/orchestrator/app/settings.py`
- `services/orchestrator/app/llm/`
- `services/orchestrator/app/planning/`
- `services/orchestrator/app/research_quality/source_intent.py`
- `services/orchestrator/app/research_quality/gap_analyzer.py`
- `services/orchestrator/app/services/search_discovery.py`
- `services/orchestrator/app/services/acquisition.py`
- `services/orchestrator/app/services/debug_pipeline.py`
- `services/orchestrator/app/services/pipeline_runtime.py`
- `services/orchestrator/app/reporting/grounded_llm.py`
- `services/orchestrator/app/api/routes/research_tasks.py`
- `packages/db/models/ledger.py`
- `task_event.payload_json`
- `research_run.checkpoint_json`
- `CandidateUrl.metadata_json`
- `ReportArtifact.manifest_json`
- `docs/architecture.md`
- `docs/runbook.md`
- `docs/api.md` in the later implementation milestone if API observability fields change

## 6. Milestones

### Milestone 1
- intent: use the existing optional planner path as the first LLM-assisted surface.
- code changes:
  - keep `RESEARCH_PLANNER_ENABLED` gated by `LLM_ENABLED`
  - tighten planner schema validation and prompt/output audit metadata if gaps remain
  - keep deterministic query guardrails as the final planner authority
  - persist prompt version, provider/model summary, raw parsed planner output, guardrail rewrites, and sanitized failure reason in task events
- validation:
  - planner unavailable falls back to deterministic/original query path
  - planner cannot remove original query or required official/reference searches
  - no LLM claims are created

### Milestone 2
- intent: introduce the LLM source judge in shadow mode only.
- code changes:
  - add a source-judge service that evaluates `CandidateUrl` rows after deterministic classification
  - validate output against the source judge schema
  - persist shadow results to candidate metadata and task events without changing fetch order
  - expose shadow diagnostics in task detail observability
- validation:
  - source selection order is unchanged when shadow mode is enabled
  - malformed LLM output is recorded and ignored
  - provider timeout/unavailable is recorded and ignored

### Milestone 3
- intent: allow active source reranking behind a separate flag.
- code changes:
  - add `LLM_SOURCE_JUDGE_ACTIVE=false` by default
  - combine deterministic priority with bounded LLM quality adjustment
  - forbid LLM promotion through deterministic guardrails
  - record pre-LLM and post-LLM ranking, including why each change was allowed or blocked
- validation:
  - LangGraph official docs and owned GitHub outrank mirrors and third-party repos
  - `freelancer.hk` and job-search URLs remain blocked/downranked even if LLM gives a high score
  - active mode can only reorder among sources that pass deterministic eligibility

### Milestone 4
- intent: add an LLM gap reasoner as a diagnostic and query-suggestion helper.
- code changes:
  - feed only slot summaries, source-yield diagnostics, selected/unattempted source summaries, and rejected evidence summaries to the LLM
  - validate structured output containing gap explanations and suggested query intents
  - deterministic gap analyzer still decides whether a gap round runs and enforces max rounds/limits
  - targeted query generation remains bounded and deduped
- validation:
  - LLM gap reasoner cannot create claims
  - LLM gap reasoner cannot bypass max rounds or fetch limits
  - provider failure leaves deterministic gap behavior unchanged

### Milestone 5
- intent: retain and harden the grounded LLM report writer as the only report-prose LLM surface.
- code changes:
  - keep deterministic Markdown as fallback and default
  - ensure the report writer receives only verified claim/evidence/citation-span bundles
  - require every LLM report item to cite valid claim/evidence/citation ids
  - persist report-writer prompt/output metadata in `ReportArtifact.manifest_json`
- validation:
  - invalid ids are dropped
  - invalid JSON falls back to deterministic Markdown
  - unsupported/draft claims do not become settled report facts

### Milestone 6
- intent: operator docs, diagnostics, and benchmark validation.
- code changes:
  - update `docs/api.md`, `docs/architecture.md`, and `docs/runbook.md` when implementation changes task-detail observability or settings
  - extend benchmark output with source-judge shadow/active summaries
  - add runbook rollback and troubleshooting steps
- validation:
  - benchmark LangGraph task still passes with no LLM
  - planner-only LLM mode passes with source judge disabled
  - source judge shadow mode produces audit output without changing selected sources

## 7. Implementation log

- 2026-05-06 / DeepSeek-assisted live acceptance fixes:
  - changes: fixed Task Detail to prefer terminal task-event `pipeline_counts` over the stale `/run` enqueue response; normalized common DeepSeek JSON aliases/fenced JSON for query rewriting, evidence reranking, and claim review before strict schema validation; expanded fallback diagnostics with validation paths and raw-output hashes; made source-judge active participation count labels/adjustments that can actually affect bounded rerank; surfaced active-rerank guardrail reasons and source-row LLM decisions; raised non-deployment overview claim caps only when required answer-slot breadth justifies it.
  - rationale: the live LangGraph acceptance run completed successfully in the ledger while the UI still showed queued zero counts, and valid-looking DeepSeek assistance outputs were falling back due to provider/schema vocabulary mismatch.
  - validation: `python3 -m py_compile ...` passed for touched orchestrator modules; `python3 -m pytest tests/unit/orchestrator/test_llm_assistance.py tests/unit/orchestrator/test_source_judge.py -q` passed; `python3 -m pytest services/orchestrator/tests/test_debug_pipeline_api.py::test_run_endpoint_queue_is_consumed_by_host_local_worker -q` passed; `npm run build` passed in `apps/web`.
  - next: rerun the live LangGraph DeepSeek acceptance and confirm `query_rewriter`, `evidence_reranker`, and `claim_reviewer` are `used` unless provider output is genuinely invalid, and that `pipeline_counts` match the ledger after completion.
- 2026-05-06 / expanded DeepSeek-assisted quality implementation:
  - changes: expanding the plan from planner/source-judge only to the full additive DeepSeek-assisted quality layer: query rewriting, optional YaCy provider, active source judging, evidence reranking, claim review, grounded report readability, configuration, diagnostics, docs, and tests.
  - rationale: real SearXNG testing shows noisy source discovery, but the project route forbids paid search APIs; DeepSeek can improve planning and ranking only as an OpenAI-compatible intelligence layer over free/self-hosted discovery and persisted evidence.
  - validation: implemented behind disabled-by-default flags with no schema migration; `python3 -m py_compile ...` passed for touched orchestrator modules; `python3 -m pytest tests/unit/orchestrator/test_llm_assistance.py tests/unit/orchestrator/test_source_judge.py tests/unit/orchestrator/test_search_helpers.py tests/unit/orchestrator/test_report_markdown.py -q` passed; `python3 -m pytest tests/unit/orchestrator/test_search_discovery_service.py tests/unit/orchestrator/test_claim_drafting_service.py tests/unit/orchestrator/test_pipeline_worker.py services/orchestrator/tests/test_research_tasks_api.py services/orchestrator/tests/test_debug_pipeline_api.py -q` passed; `python3 -m pytest tests/unit/orchestrator/test_report_synthesis_service.py -q` passed; `python3 -m ruff check ...` passed for touched Python files; `npm run build` passed in `apps/web`; `git diff --check` passed. `npm run lint` could not run because `eslint` is not installed in the current `apps/web` environment.
  - next: run live DeepSeek acceptance from the configured operator shell, then tune prompts and thresholds from real LangGraph/SearXNG traces.
- 2026-04-30 / planning:
  - changes: created this ExecPlan and updated architecture/runbook docs only.
  - rationale: the next work spans planner, search discovery, acquisition ranking, gap behavior, reporting, settings, observability, and tests; implementation needs a staged plan before code changes.
  - validation: documentation-only checks pending.
  - next: review plan, then implement Milestone 1 only.
- 2026-04-30 / Milestone 1 implementation:
  - changes: implemented optional LLM planner strict JSON validation, deterministic fallback for invalid/unavailable providers, planner source/status diagnostics in task events and observability, and targeted planner/API/pipeline tests.
  - rationale: planner-only is the first LLM-assisted surface; source judge, gap reasoner, and report-writer behavior remain out of scope.
  - validation: targeted planner/provider/API/pipeline pytest passed; ruff, black --check, and `git diff --check` passed.
  - next: keep source judge work deferred to Milestone 2 shadow mode.
- 2026-04-30 / Milestone 1 runtime acceptance follow-up:
  - changes: tightened planner JSON extraction and expected-source-type validation, added parse-stage and schema-error diagnostics, preserved fallback diagnostics through operator-edited plan events, changed successful LLM provenance to `planner_status=success` / `plan_source=llm_planner`, and updated planner warning text plus docs/tests.
  - rationale: planner-only runtime acceptance needs strict JSON handling, actionable schema diagnostics, and correct planner provenance without broadening into source judge, gap reasoner, or report-writer work.
  - validation: targeted planner/provider/API/debug-pipeline tests passed; full research-task API and debug-pipeline suites passed; `python3 -m ruff check .`, `python3 -m black --check .`, and `git diff --check` passed. Live DeepSeek planner-only rerun was not possible in this shell because required LLM environment variables were absent.
  - next: rerun DeepSeek planner acceptance from the configured runtime shell; if it still falls back, inspect `research_plan.planner_diagnostics.validation_errors`, `raw_output_preview`, and `json_extraction_error`.
- 2026-04-30 / Milestone 1 full-runtime acceptance follow-up:
  - changes: applied deterministic LangGraph owned-source guardrails after successful LLM planning, preserved mechanism/site/GitHub/trust guardrail queries, added planner-domain correction diagnostics, added deterministic LangGraph known-path candidates, and made supplemental gap search outages non-fatal when the task already has usable evidence for a partial report.
  - rationale: the accepted DeepSeek plan was schema-valid but weaker than deterministic planning; generic sources were attempted before owned docs/reference/upstream GitHub, then a SearXNG gap-round outage failed the whole task despite existing claims and chunks.
  - validation: targeted planner/source-selection/gap/research-task tests passed; `python3 -m ruff check .`, `python3 -m black --check .`, and `git diff --check` passed. Async `/run` live smoke created task `f9944264-5dce-468e-b739-f2f292ceb088` but failed in the existing worker before planning with `UnicodeEncodeError`; a synchronous real-pipeline fallback created task `61f0134a-988d-44bc-a0f7-769e7bf2ce40` and completed with `planner_status=success`, `plan_source=llm_planner`, `schema_validated=true`, preserved LangGraph guardrail queries, and official docs/reference attempts before generic sources.
  - next: source judge, LLM gap reasoner, and report-writer changes remain deferred.
- 2026-04-30 / Milestone 1 main-search fallback follow-up:
  - changes: implemented main `SEARCHING` deterministic known-path fallback for LangGraph when SearXNG raises `searxng_empty_results_with_unresponsive_engines`, added fallback query diagnostics and candidate provenance, exposed search-query/fallback observability in task detail, and scoped LangGraph product-page ranking behind docs/reference/upstream GitHub.
  - rationale: webpage async planner-LLM runs now reach a good guarded plan but fail before acquisition because the first SearXNG call can report empty results with unresponsive engines even though immediate manual SearXNG checks return LangGraph results.
  - validation: targeted search-discovery/debug-pipeline/acquisition/source-quality/gap tests passed; `python3 -m ruff check .`, `python3 -m black --check .`, and `git diff --check` passed. Live LangGraph smoke task `4e1cd59f-e8e4-4eef-8e9f-78c28329f2e1` completed in `real-search+opensearch+planner-LLM`; SearXNG returned usable candidates so known-path fallback was not needed, attempted sources included `docs.langchain.com` and `github.com/langchain-ai/langgraph` before generic sources, and planner fell back because the worker process lacked `LLM_API_KEY`.
  - next: keep the fallback deterministic and bounded; source judge, report writer, `.env`, and worker architecture remain out of scope.

## 8. Validation

Planning-turn validation:

- `git diff --check` should pass.
- No runtime settings are changed.
- No `.env` or secret files are edited.

Implementation validation for later milestones:

- `python3 -m pytest tests/unit/orchestrator/test_research_planner.py -q`
- `python3 -m pytest tests/unit/orchestrator/test_research_quality.py tests/unit/orchestrator/test_acquisition_service.py -q`
- `python3 -m pytest tests/unit/orchestrator/test_llm_source_judge.py -q`
- `python3 -m pytest services/orchestrator/tests/test_debug_pipeline_api.py -q`
- `python3 -m ruff check <touched files>`
- `python3 -m black --check <touched files>`
- `git diff --check`
- no-LLM regression:
  - `python3 scripts/benchmark_queries.py --run --base-url http://127.0.0.1:8000 --query-id 3 --wait-seconds 420 --output /tmp/deepsearch-langgraph-no-llm-regression.json`
- planner-only smoke:
  - enable planner in shell environment only, not `.env`
  - confirm source judge remains disabled
  - confirm claims and reports remain evidence-backed

## 9. Risks and unknowns

- LLM source judgment can be persuasive but wrong, especially around unofficial mirrors and third-party repositories.
- Provider behavior can vary over time; strict schema validation and stored prompt/output metadata are required for audit.
- Active reranking can reduce reproducibility if score-combination rules are not deterministic and versioned.
- Persisting all raw LLM prompts and responses in task events may increase task-event size; implementation may need compact raw output hashes plus stored diagnostic artifacts if payloads grow too large.
- Source ownership evidence needs a curated project registry. LLM inference must not replace it.
- Gap reasoner suggestions can overfit to missing slots and propose broad searches. Deterministic query caps and dedupe must remain final.

## 10. Rollback / recovery

- Roll back by disabling feature flags first:
  - `RESEARCH_PLANNER_ENABLED=false`
  - future `LLM_SOURCE_JUDGE_ENABLED=false`
  - future `LLM_SOURCE_JUDGE_ACTIVE=false`
  - future `LLM_GAP_REASONER_ENABLED=false`
  - `LLM_REPORT_WRITER_ENABLED=false`
- If source judge active mode causes bad ranking, set it back to shadow mode and keep persisted diagnostics for analysis.
- If provider errors are noisy, disable `LLM_ENABLED` or the narrower component flag.
- If a schema migration is later added for source-judge audit tables, ship it in a separate milestone with explicit downgrade/backfill notes. This planning turn adds no migration.
- Existing tasks must remain readable because LLM metadata is additive and defaults to missing/empty values in observability.

## 11. Deferred work

- Dedicated source-judge audit table after JSON task-event/candidate metadata proves insufficient.
- Model-specific prompt tuning.
- Human review UI for source-judge disagreements.
- Cross-run source reputation cache.
- Embedding or hybrid retrieval reranking.
- Browser/PDF/Tika support.

## Architecture Summary

The LLM-assisted quality layer sits beside the deterministic pipeline. It does not replace any ledger object or workflow stage.

Planner:

- input: task query, constraints, current deterministic planner context
- output: bounded subquestions and search queries
- final authority: deterministic planner guardrails
- persistence: `research_plan.created` / `research_plan.failed` task events

Source judge:

- input: candidate URL metadata plus deterministic source-intent classification and ownership evidence
- output: structured advisory label, topicality, authority, confidence, rationale, and suggested bounded ranking adjustment
- final authority: deterministic source-intent guardrails, ownership registry, blocklist, and acquisition limits
- persistence: candidate metadata plus task-event source-judge summaries

Gap reasoner:

- input: slot coverage, source yield, rejected evidence summaries, failed/unattempted source summaries
- output: structured gap explanation and suggested query intents
- final authority: deterministic gap analyzer, max rounds, fetch limits, dedupe, and low-value filters
- persistence: gap-analysis task events

Grounded report writer:

- input: verified claims, claim evidence, citation spans, source summaries
- output: structured report items referencing valid ids
- final authority: report bundle validator and deterministic Markdown fallback
- persistence: `report_artifact.manifest_json`

## Source Judge Proposed Schemas

### Input schema

```json
{
  "schema_version": "llm_source_judge_input_v1",
  "task": {
    "task_id": "uuid",
    "query": "What is LangGraph and how does it work?",
    "intent": "definition_how_it_works",
    "subject_terms": ["langgraph"],
    "answer_slots": [
      {
        "slot_id": "definition",
        "label": "What it is",
        "required": true
      }
    ]
  },
  "candidate": {
    "candidate_url_id": "uuid",
    "canonical_url": "https://docs.langchain.com/oss/python/langgraph/overview",
    "domain": "docs.langchain.com",
    "title": "LangGraph overview - Docs by LangChain",
    "snippet": "Search result snippet text, bounded.",
    "rank": 1,
    "search_query_text": "LangGraph official documentation",
    "known_path_candidate": false
  },
  "deterministic": {
    "source_category": "official_about",
    "fetch_priority_score": 0,
    "source_quality_score": 0.95,
    "downrank_reason": null,
    "blocked": false,
    "low_value_signals": [],
    "ownership": {
      "owned_domain_match": true,
      "owned_github_repo_match": false,
      "secondary_domain_match": false,
      "project_profile": "langgraph",
      "evidence": ["domain_suffix:langchain.com"]
    }
  },
  "policy": {
    "allowed_labels": [
      "official_owned",
      "official_reference",
      "upstream_repository",
      "secondary_reference",
      "generic_explainer",
      "tutorial_or_blog",
      "community_forum",
      "social_video",
      "job_or_listing",
      "seo_repost_or_scrape",
      "off_topic",
      "blocked_low_value",
      "unknown"
    ],
    "official_requires_deterministic_ownership": true,
    "blocklist_wins": true
  }
}
```

### Output schema

```json
{
  "schema_version": "llm_source_judge_output_v1",
  "candidate_url_id": "uuid",
  "label": "official_reference",
  "topicality_score": 0.0,
  "authority_score": 0.0,
  "usefulness_score": 0.0,
  "risk_score": 0.0,
  "confidence": 0.0,
  "officialness_confidence": 0.0,
  "suggested_quality_score": 0.0,
  "suggested_priority_delta": 0,
  "should_fetch": true,
  "rationale": "One or two sentences explaining the judgment from the provided metadata only.",
  "evidence_refs": [
    "domain",
    "title",
    "snippet",
    "deterministic_ownership"
  ],
  "concerns": [
    "none"
  ]
}
```

Validation rules:

- `schema_version` must match exactly.
- `candidate_url_id` must match the input candidate.
- `label` must be one allowed label.
- scores must be floats in `[0.0, 1.0]`.
- `suggested_priority_delta` must be an integer in `[-10, 10]`.
- `rationale` is required and capped, for example 500 characters.
- `evidence_refs` may only name fields present in the input.
- extra fields are rejected.
- invalid output is ignored and recorded as `llm_source_judge_invalid_output`.

### Allowed labels

- `official_owned`: deterministic ownership evidence says the domain or repo is owned by the queried project.
- `official_reference`: deterministic ownership evidence says this is owned reference/docs material.
- `upstream_repository`: deterministic ownership evidence says this is the upstream project repo.
- `secondary_reference`: useful but not official-owned, including localized mirrors.
- `generic_explainer`: topical generic article from a normal publisher.
- `tutorial_or_blog`: topical tutorial, blog, or walkthrough.
- `community_forum`: Reddit, forum, Q&A, community discussion.
- `social_video`: video/social result.
- `job_or_listing`: job board, freelance, hiring, directory, or listing page.
- `seo_repost_or_scrape`: obvious repost, scraped docs, SEO aggregation, or download farm.
- `off_topic`: does not answer the task subject.
- `blocked_low_value`: deterministic low-value/blocklist category or policy-blocked source.
- `unknown`: insufficient metadata.

### Score combination

The deterministic source score remains primary. Active source judging can only apply a bounded adjustment after guardrails.

Suggested algorithm:

```text
if deterministic.blocked or deterministic.source_category == "low_quality_or_blocked":
    final_priority = max(deterministic_priority, 99)
    final_quality = min(deterministic_quality, 0.10)
    decision = "deterministic_blocklist_wins"
elif output.label in official labels and deterministic ownership evidence is missing:
    ignore official label
    final_priority = deterministic_priority
    final_quality = deterministic_quality
    decision = "llm_official_claim_rejected_no_ownership"
elif source_judge_active:
    allowed_delta = clamp(output.suggested_priority_delta, -10, 10)
    if deterministic_priority <= 12:
        allowed_delta = min(allowed_delta, 0)
    final_priority = clamp_priority(deterministic_priority + allowed_delta)
    final_quality = clamp(
        (deterministic_quality * 0.75) + (output.suggested_quality_score * 0.25)
    )
    decision = "llm_adjustment_applied"
else:
    final_priority = deterministic_priority
    final_quality = deterministic_quality
    decision = "shadow_only"
```

Additional guardrails:

- LLM cannot move `secondary_reference` above owned official docs/reference/GitHub.
- LLM cannot move third-party GitHub tutorial repos into `github_readme_or_repo`.
- LLM cannot move job/freelance/listing pages into the fetch set for overview queries.
- LLM cannot increase a source above deterministic official-owned candidates.
- LLM cannot override SSRF, MIME, fetch, parse, or acquisition policy.

### Audit metadata

Persist enough data to reproduce or review the judgment:

- `llm_source_judge_enabled`
- `llm_source_judge_mode`: `shadow` or `active`
- `llm_source_judge_schema_version`
- `llm_source_judge_prompt_version`
- provider name and model name, without API key
- input hash and bounded input snapshot
- raw output hash and parsed output
- validation status and validation errors
- deterministic pre-judge category, priority, quality, and guardrail reasons
- post-judge priority, quality, and decision reason
- whether an LLM suggestion was applied or blocked
- sanitized provider error if unavailable

Initial persistence target:

- `CandidateUrl.metadata_json["llm_source_judge"]` for per-candidate details
- `task_event.payload_json.result["source_judge_summary"]` for stage-level summaries
- `research_run.checkpoint_json["last_source_judge_summary"]` for resume diagnostics

Only add a relational audit table later if JSON payload size or query needs justify it.

## Rollout Checklist

- [ ] Milestone 1: planner-only LLM mode, deterministic guardrails final.
- [ ] Milestone 2: source judge shadow mode, no ranking changes.
- [ ] Milestone 3: source judge active reranking behind a separate flag.
- [ ] Milestone 4: LLM gap reasoner, deterministic gap analyzer final.
- [ ] Milestone 5: grounded report writer hardening, deterministic Markdown fallback.
- [ ] Milestone 6: docs, benchmark output, runbook troubleshooting, rollback checks.

## Test Plan

- LangGraph official docs vs mirrors:
  - `docs.langchain.com`, `reference.langchain.com`, and `langchain.com/langgraph` remain owned/high value.
  - `github.langchain.ac.cn`, `langgraph.com.cn`, and `langchain-doc.cn` remain `secondary_reference`.
- Official GitHub repo vs third-party tutorial repos:
  - `github.com/langchain-ai/langgraph` is upstream.
  - `github.com/datawhalechina/easy-langent`, `github.com/aneasystone/weekly-practice`, and similar repos are not upstream.
- Job/freelance filtering:
  - `freelancer.hk` and `/job-search/` URLs stay low value even if LLM output says useful.
- Source judge schema validation:
  - invalid labels, extra fields, missing rationale, out-of-range scores, and mismatched candidate ids are rejected.
- Provider fallback:
  - timeout, invalid JSON, and provider errors leave deterministic ranking unchanged.
- Shadow mode:
  - selected/attempted source order is byte-for-byte equivalent to no-source-judge mode, except for additive diagnostics.
- Active mode:
  - active reranking only reorders deterministic-eligible candidates and records all changes.
- Report writer:
  - invalid claim/evidence/citation ids are dropped.
  - unsupported claims never appear as settled facts.
