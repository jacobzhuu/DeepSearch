from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from packages.db.models import ResearchTask
from services.orchestrator.app.research_quality.llm_research_strategist import (
    LLMResearchStrategistService,
)
from services.orchestrator.app.services.debug_pipeline import DebugRealPipelineRunner


@pytest.fixture
def mock_session() -> MagicMock:
    return MagicMock(spec=Session)


@pytest.fixture
def mock_task() -> MagicMock:
    task = MagicMock(spec=ResearchTask)
    task.id = uuid4()
    task.query = "What is a token?"
    task.status = "QUEUED"
    task.constraints_json = {}
    task.started_at = None
    return task


def test_strategist_continue_search_triggers_round(
    mock_session: MagicMock, mock_task: MagicMock
) -> None:
    # Mock repositories
    mock_task_repo = MagicMock()
    mock_task_repo.get.return_value = mock_task

    mock_strategist = MagicMock(spec=LLMResearchStrategistService)
    mock_strategist.enabled = True
    mock_strategist.decide.return_value = MagicMock(
        status="used",
        decision="continue_search",
        planned_queries=[MagicMock(query_text="new query", query_source="llm_research_strategist")],
        to_payload=lambda: {
            "status": "used",
            "decision": "continue_search",
            "planned_queries": [{"query_text": "new query"}],
        },
    )

    runner = DebugRealPipelineRunner(
        mock_session,
        search_service=MagicMock(),
        acquisition_service=MagicMock(),
        parsing_service=MagicMock(),
        indexing_service=MagicMock(),
        claims_service=MagicMock(),
        reporting_service=MagicMock(),
        research_strategist_service=mock_strategist,
        dependencies={},
        research_loop_enabled=True,
        research_loop_strategist_shadow_mode=False,
        max_gap_rounds=1,
    )
    runner.task_repository = mock_task_repo
    runner.run_repository = MagicMock()
    runner.event_repository = MagicMock()

    # Mock internal methods to isolate the loop
    runner._run_planner_if_configured = MagicMock()  # type: ignore
    runner._run_search = MagicMock(return_value={})  # type: ignore
    runner._run_fetch = MagicMock(return_value={"fetch_succeeded": 1})  # type: ignore
    runner._run_parse = MagicMock(return_value={})  # type: ignore
    runner._run_index = MagicMock(return_value={})  # type: ignore
    runner._run_draft_claims = MagicMock(return_value={})  # type: ignore
    runner._run_verify_claims = MagicMock(return_value={})  # type: ignore
    runner._run_report = MagicMock(return_value={})  # type: ignore

    def mock_execute_stage(
        task_id: UUID,
        stage: str,
        action: Callable[[UUID], dict[str, Any]],
        stages_completed: list[str],
    ) -> None:
        action(task_id)

    runner._execute_stage = MagicMock(side_effect=mock_execute_stage)  # type: ignore
    runner._current_slot_coverage_summary = MagicMock(  # type: ignore
        return_value=[{"slot_id": "def", "required": True, "status": "missing"}]
    )
    runner._current_coverage_evaluation = MagicMock(return_value={"can_stop": False})  # type: ignore

    # Mock _run_research_more_round to avoid deep nesting
    runner._run_research_more_round = MagicMock(return_value={"status": "ok"})  # type: ignore

    # We want to see if the loop continues and calls _run_research_more_round
    # Since we set max_gap_rounds=1, it should run at least one round if triggered.

    runner.run(mock_task.id)

    # Verify strategist was called
    assert mock_strategist.decide.called
    # Verify a research more round was triggered
    assert runner._run_research_more_round.called
    # Verify the decision was passed (implicitly via payload)
    args, kwargs = runner._run_research_more_round.call_args
    assert args[1]["strategy_decision"] == "continue_search"


def test_strategist_fetch_more_existing_candidates_triggers_round(
    mock_session: MagicMock, mock_task: MagicMock
) -> None:
    mock_task_repo = MagicMock()
    mock_task_repo.get.return_value = mock_task

    mock_strategist = MagicMock(spec=LLMResearchStrategistService)
    mock_strategist.enabled = True
    mock_strategist.decide.return_value = MagicMock(
        status="used",
        decision="fetch_more_existing_candidates",
        planned_queries=[],
        to_payload=lambda: {
            "status": "used",
            "decision": "fetch_more_existing_candidates",
            "planned_queries": [],
        },
    )

    runner = DebugRealPipelineRunner(
        mock_session,
        search_service=MagicMock(),
        acquisition_service=MagicMock(),
        parsing_service=MagicMock(),
        indexing_service=MagicMock(),
        claims_service=MagicMock(),
        reporting_service=MagicMock(),
        research_strategist_service=mock_strategist,
        dependencies={},
        research_loop_enabled=True,
        research_loop_strategist_shadow_mode=False,
        max_gap_rounds=1,
    )
    runner.task_repository = mock_task_repo
    runner.run_repository = MagicMock()
    runner.event_repository = MagicMock()
    runner._run_planner_if_configured = MagicMock()  # type: ignore
    runner._run_search = MagicMock(return_value={})  # type: ignore
    runner._run_fetch = MagicMock(return_value={"fetch_succeeded": 1})  # type: ignore
    runner._run_parse = MagicMock(return_value={})  # type: ignore
    runner._run_index = MagicMock(return_value={})  # type: ignore
    runner._run_draft_claims = MagicMock(return_value={})  # type: ignore
    runner._run_verify_claims = MagicMock(return_value={})  # type: ignore
    runner._run_report = MagicMock(return_value={})  # type: ignore

    def mock_execute_stage(
        task_id: UUID,
        stage: str,
        action: Callable[[UUID], dict[str, Any]],
        stages_completed: list[str],
    ) -> None:
        action(task_id)

    runner._execute_stage = MagicMock(side_effect=mock_execute_stage)  # type: ignore
    runner._current_slot_coverage_summary = MagicMock(  # type: ignore
        return_value=[{"slot_id": "def", "required": True, "status": "missing"}]
    )
    runner._current_coverage_evaluation = MagicMock(return_value={"can_stop": False})  # type: ignore
    runner._run_research_more_round = MagicMock(return_value={"status": "ok"})  # type: ignore

    runner.run(mock_task.id)

    assert runner._run_research_more_round.called
    args, kwargs = runner._run_research_more_round.call_args
    assert args[1]["strategy_decision"] == "fetch_more_existing_candidates"


def test_strategist_stop_sufficient_does_not_stop_when_coverage_is_weak(
    mock_session: MagicMock, mock_task: MagicMock
) -> None:
    mock_task_repo = MagicMock()
    mock_task_repo.get.return_value = mock_task

    mock_strategist = MagicMock(spec=LLMResearchStrategistService)
    mock_strategist.enabled = True
    mock_strategist.decide.return_value = MagicMock(
        status="used",
        decision="stop_sufficient",
        planned_queries=[],
        to_payload=lambda: {
            "status": "used",
            "decision": "stop_sufficient",
            "planned_queries": [],
        },
    )

    runner = DebugRealPipelineRunner(
        mock_session,
        search_service=MagicMock(),
        acquisition_service=MagicMock(),
        parsing_service=MagicMock(),
        indexing_service=MagicMock(),
        claims_service=MagicMock(),
        reporting_service=MagicMock(),
        research_strategist_service=mock_strategist,
        dependencies={},
        research_loop_enabled=True,
        research_loop_strategist_shadow_mode=False,
        max_gap_rounds=5,
    )
    runner.task_repository = mock_task_repo
    runner.run_repository = MagicMock()
    runner.event_repository = MagicMock()
    runner._run_planner_if_configured = MagicMock()  # type: ignore
    runner._run_search = MagicMock(return_value={})  # type: ignore
    runner._run_fetch = MagicMock(return_value={"fetch_succeeded": 1})  # type: ignore
    runner._run_parse = MagicMock(return_value={})  # type: ignore
    runner._run_index = MagicMock(return_value={})  # type: ignore
    runner._run_draft_claims = MagicMock(return_value={})  # type: ignore
    runner._run_verify_claims = MagicMock(return_value={})  # type: ignore
    runner._run_report = MagicMock(return_value={})  # type: ignore

    def mock_execute_stage(
        task_id: UUID,
        stage: str,
        action: Callable[[UUID], dict[str, Any]],
        stages_completed: list[str],
    ) -> None:
        action(task_id)

    runner._execute_stage = MagicMock(side_effect=mock_execute_stage)  # type: ignore
    runner._current_slot_coverage_summary = MagicMock(  # type: ignore
        return_value=[{"slot_id": "def", "required": True, "status": "missing"}]
    )
    runner._current_coverage_evaluation = MagicMock(return_value={"can_stop": False})  # type: ignore
    runner._run_research_more_round = MagicMock(return_value={"status": "ok"})  # type: ignore

    runner.run(mock_task.id)

    assert runner._run_research_more_round.called


def test_report_quality_gate_triggers_followup_when_slots_covered_but_claims_thin(
    mock_session: MagicMock, mock_task: MagicMock
) -> None:
    mock_task_repo = MagicMock()
    mock_task_repo.get.return_value = mock_task

    runner = DebugRealPipelineRunner(
        mock_session,
        search_service=MagicMock(),
        acquisition_service=MagicMock(),
        parsing_service=MagicMock(),
        indexing_service=MagicMock(),
        claims_service=MagicMock(),
        reporting_service=MagicMock(),
        dependencies={},
        research_loop_enabled=True,
        max_gap_rounds=1,
    )
    runner.task_repository = mock_task_repo
    runner.run_repository = MagicMock()
    runner.event_repository = MagicMock()
    runner._run_planner_if_configured = MagicMock()  # type: ignore
    runner._run_search = MagicMock(return_value={})  # type: ignore
    runner._run_fetch = MagicMock(return_value={"fetch_succeeded": 1})  # type: ignore
    runner._run_parse = MagicMock(return_value={})  # type: ignore
    runner._run_index = MagicMock(return_value={})  # type: ignore
    runner._run_draft_claims = MagicMock(return_value={})  # type: ignore
    runner._run_verify_claims = MagicMock(return_value={})  # type: ignore
    runner._run_report = MagicMock(return_value={})  # type: ignore

    def mock_execute_stage(
        task_id: UUID,
        stage: str,
        action: Callable[[UUID], dict[str, Any]],
        stages_completed: list[str],
    ) -> None:
        action(task_id)

    runner._execute_stage = MagicMock(side_effect=mock_execute_stage)  # type: ignore
    runner._current_slot_coverage_summary = MagicMock(  # type: ignore
        return_value=[{"slot_id": "definition", "required": True, "status": "covered"}]
    )
    runner._current_coverage_evaluation = MagicMock(return_value={"can_stop": True})  # type: ignore
    runner._current_report_quality_gate = MagicMock(  # type: ignore
        side_effect=[
            {
                "status": "insufficient",
                "triggered": True,
                "reason": "report_quality_below_threshold",
                "metrics": {"missing_required_slots": [], "weak_required_slots": []},
            },
            {
                "status": "insufficient",
                "triggered": False,
                "reason": "report_quality_below_threshold",
                "metrics": {"missing_required_slots": [], "weak_required_slots": []},
            },
        ]
    )
    runner._existing_search_query_texts = MagicMock(return_value=set())  # type: ignore
    runner._run_research_more_round = MagicMock(return_value={"status": "ok"})  # type: ignore

    runner.run(mock_task.id)

    assert runner._run_research_more_round.called
    args, _ = runner._run_research_more_round.call_args
    assert args[1]["reason"] == "report_quality_gate_insufficient"
    assert args[1]["supplemental_queries"]
