#!/bin/bash
# scripts/live_regression_research_loop.sh
# Live regression for active research loop semantics.
# Question: 什么是LLM中的token？

set -e

echo "Starting live regression for active research loop..."

# Ensure we are in development mode
export APP_ENV=development

# Enable active research loop with strategist
export RESEARCH_LOOP_ENABLED=true
export RESEARCH_LOOP_STRATEGIST_ENABLED=true
export RESEARCH_LOOP_STRATEGIST_SHADOW_MODE=false
export LLM_SOURCE_JUDGE_ENABLED=true
export LLM_SOURCE_TRIAGE_ACTIVE=true

# Set some conservative budgets for the test
export RESEARCH_LOOP_MAX_ROUNDS=2
export RESEARCH_LOOP_MAX_TOTAL_QUERIES=10
export RESEARCH_LOOP_FETCH_MORE_CANDIDATES_PER_ROUND=2

QUERY="什么是LLM中的token？"

echo "Running DeepSearch with active strategist for: $QUERY"

# Run smoke test or use the actual research_worker script if available
# For this regression, we'll use scripts/smoke_test.py if it supports these flags
# or we can run the research worker directly on a task.

# Since we don't want to actually call expensive LLMs if not needed for local regression,
# this script is mostly documented commands.

# To actually run it:
# python scripts/smoke_test.py --query "$QUERY" --active-loop

echo "To run this live regression, use the following environment variables:"
echo "------------------------------------------------------------------"
echo "export RESEARCH_LOOP_ENABLED=true"
echo "export RESEARCH_LOOP_STRATEGIST_ENABLED=true"
echo "export RESEARCH_LOOP_STRATEGIST_SHADOW_MODE=false"
echo "export LLM_SOURCE_JUDGE_ENABLED=true"
echo "export LLM_SOURCE_TRIAGE_ACTIVE=true"
echo "------------------------------------------------------------------"
echo "Then run:"
echo "python scripts/smoke_test.py --query \"$QUERY\""

echo ""
echo "Expected diagnostics should include:"
echo "- strategist decision per round (e.g. continue_search, fetch_more_existing_candidates, stop_sufficient)"
echo "- coverage score per round"
echo "- attempted / unattempted / skipped candidate counts"
echo "- whether final report was coverage_sufficient or partial"
