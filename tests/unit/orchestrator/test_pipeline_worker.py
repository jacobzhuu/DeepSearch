from __future__ import annotations

import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Protocol
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from packages.db.repositories import (
    CandidateUrlRepository,
    ResearchTaskRepository,
    TaskEventRepository,
)
from services.orchestrator.app.planning import PlannedSearchQuery, ResearchPlan
from services.orchestrator.app.search import (
    SearchProviderError,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
    SimpleQueryExpansionStrategy,
)
from services.orchestrator.app.services.debug_pipeline import (
    SEARCH_ALLOWED_STATUSES,
    DebugPipelinePreconditionError,
    DebugRealPipelineRunner,
)
from services.orchestrator.app.services.pipeline_worker import ResearchPipelineWorker
from services.orchestrator.app.services.research_tasks import (
    PHASE2_ACTIVE_STATUS,
    RUNTIME_QUEUED_STATUS,
    build_task_event_payload,
    create_research_task_service,
)
from services.orchestrator.app.services.search_discovery import create_search_discovery_service
from services.orchestrator.app.settings import Settings


class _TestPipelineRunner(Protocol):
    def run(self, task_id: UUID) -> SimpleNamespace: ...


def test_worker_runs_queued_task_with_injected_runner(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        service = create_research_task_service(session)
        task = service.create_task(query="Worker task", constraints={})
        service.enqueue_task(task.id)
        task_id = task.id

    worker = ResearchPipelineWorker(
        session_factory=session_factory,
        settings=Settings(search_provider="smoke", index_backend="local"),
        runner_factory=_complete_task_runner,
    )

    assert worker.run_once() == 1

    with session_factory() as session:
        loaded_task = ResearchTaskRepository(session).get(task_id)
        assert loaded_task is not None
        assert loaded_task.status == "COMPLETED"
        event_types = [
            event.event_type for event in TaskEventRepository(session).list_for_task(task_id)
        ]
        assert "pipeline.queued" in event_types
        assert "pipeline.completed" in event_types
        ResearchTaskRepository(session).set_status(
            loaded_task,
            PHASE2_ACTIVE_STATUS,
            ended_at=None,
        )
        session.commit()


def test_worker_requeues_interrupted_runtime_status(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        service = create_research_task_service(session)
        task = service.create_task(query="Interrupted task", constraints={})
        ResearchTaskRepository(session).set_status(task, "SEARCHING", ended_at=None)
        session.commit()
        task_id = task.id

    worker = ResearchPipelineWorker(
        session_factory=session_factory,
        settings=Settings(search_provider="smoke", index_backend="local"),
        runner_factory=_complete_task_runner,
    )

    assert worker.recover_interrupted_tasks() == 1

    with session_factory() as session:
        loaded_task = ResearchTaskRepository(session).get(task_id)
        assert loaded_task is not None
        assert loaded_task.status == RUNTIME_QUEUED_STATUS
        events = TaskEventRepository(session).list_for_task(task_id)
        assert events[-1].event_type == "pipeline.requeued"
        assert events[-1].payload_json["from_status"] == "SEARCHING"
        ResearchTaskRepository(session).set_status(
            loaded_task,
            PHASE2_ACTIVE_STATUS,
            ended_at=None,
        )
        session.commit()


def test_pipeline_leaves_searching_when_later_search_failure_has_existing_candidates(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        task_service = create_research_task_service(session)
        task = task_service.create_task(
            query="What is LangGraph and how does it work?",
            constraints={},
        )
        task_service.enqueue_task(task.id)
        task_id = task.id

    worker = ResearchPipelineWorker(
        session_factory=session_factory,
        settings=Settings(search_provider="smoke", index_backend="local"),
        runner_factory=_partial_search_then_fetch_failure_runner,
    )

    assert worker.run_once() == 1

    with session_factory() as session:
        loaded_task = ResearchTaskRepository(session).get(task_id)
        assert loaded_task is not None
        assert loaded_task.status == "FAILED"
        events = TaskEventRepository(session).list_for_task(task_id)
        stage_events = [
            event.payload_json for event in events if event.event_type == "pipeline.stage_started"
        ]
        assert [event["stage"] for event in stage_events] == ["SEARCHING", "ACQUIRING"]
        assert events[-1].event_type == "pipeline.failed"
        assert events[-1].payload_json["from_status"] == "ACQUIRING"
        assert CandidateUrlRepository(session).list_for_task(task_id)
        ResearchTaskRepository(session).set_status(
            loaded_task,
            PHASE2_ACTIVE_STATUS,
            ended_at=None,
        )
        session.commit()


class _StopForever(Exception):
    """Test sentinel to break the infinite worker loop."""


def test_run_forever_survives_poll_operational_error_then_polls_again(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    poll_calls = {"n": 0}

    def flaky_run_once(self: ResearchPipelineWorker) -> int:
        poll_calls["n"] += 1
        if poll_calls["n"] == 1:
            raise OperationalError("SELECT 1", {}, RuntimeError("simulated schema drift"))
        return 0

    monkeypatch.setattr(ResearchPipelineWorker, "run_once", flaky_run_once)

    sleeps = {"n": 0}

    def sleep_side(_duration: float) -> None:
        sleeps["n"] += 1
        if sleeps["n"] >= 2:
            raise _StopForever()

    monkeypatch.setattr(time, "sleep", sleep_side)

    worker = ResearchPipelineWorker(
        session_factory=session_factory,
        settings=Settings(search_provider="smoke", index_backend="local"),
        runner_factory=_complete_task_runner,
    )
    monkeypatch.setattr(worker, "recover_interrupted_tasks", lambda: 0)

    with pytest.raises(_StopForever):
        worker.run_forever()

    assert poll_calls["n"] == 2
    assert sleeps["n"] == 2


def _complete_task_runner(session: Session) -> _TestPipelineRunner:
    class _Runner:
        def run(self, task_id: UUID) -> SimpleNamespace:
            task_repository = ResearchTaskRepository(session)
            event_repository = TaskEventRepository(session)
            task = task_repository.get(task_id)
            assert task is not None
            from_status = task.status
            task_repository.set_status(task, "COMPLETED", ended_at=datetime.now(UTC))
            event_repository.record(
                task_id=task.id,
                event_type="pipeline.completed",
                payload_json={
                    **build_task_event_payload(from_status=from_status, to_status="COMPLETED"),
                    "source": "test.worker",
                    "stage": "COMPLETED",
                },
            )
            session.commit()
            session.refresh(task)
            return SimpleNamespace(completed=True, task=task)

    return _Runner()


def _partial_search_then_fetch_failure_runner(session: Session) -> _TestPipelineRunner:
    provider = _PartialLangGraphFailureSearchProvider()
    search_service = create_search_discovery_service(
        session,
        search_provider=provider,
        query_expansion_strategy=SimpleQueryExpansionStrategy(max_domain_expansions=0),
        max_results_per_query=5,
        allowed_statuses=SEARCH_ALLOWED_STATUSES,
    )
    runner = DebugRealPipelineRunner(
        session,
        search_service=search_service,
        acquisition_service=object(),
        parsing_service=object(),
        indexing_service=_CountingIndexingService(),
        claims_service=object(),
        reporting_service=object(),
        planner_service=None,
        source_judge_service=None,
        dependencies={
            "search_mode": "real-search",
            "index_mode": "opensearch",
            "llm_mode": "planner+report-LLM",
        },
        max_gap_rounds=0,
    )
    runner.research_plan = ResearchPlan(
        intent="definition_how_it_works",
        normalized_question="What is LangGraph and how does it work?",
        subquestions=[],
        search_queries=[
            PlannedSearchQuery(
                query_text="LangGraph official docs overview",
                rationale="owned docs",
                expected_source_type="official_docs",
                priority=1,
            ),
            PlannedSearchQuery(
                query_text="LangGraph state graph reference",
                rationale="owned reference",
                expected_source_type="reference",
                priority=2,
            ),
            PlannedSearchQuery(
                query_text="LangGraph GitHub repository",
                rationale="upstream repository",
                expected_source_type="official_repository",
                priority=3,
            ),
        ],
        source_preferences={},
        answer_outline=[],
        risk_notes=[],
        planner_mode="deterministic",
        intent_classification="overview_definition_intent",
    )

    def fail_fetch(task_id: UUID) -> dict[str, object]:
        raise DebugPipelinePreconditionError("forced fetch stop after search regression check")

    runner._run_fetch = fail_fetch  # type: ignore[method-assign]
    return runner


class _CountingIndexingService:
    def list_indexed_chunks(self, task_id: UUID, *, offset: int, limit: int) -> SimpleNamespace:
        return SimpleNamespace(total=0)


class _PartialLangGraphFailureSearchProvider:
    name = "searxng"

    def search(self, request: SearchRequest) -> SearchResponse:
        if request.query_text == "LangGraph state graph reference":
            raise SearchProviderError(
                reason="searxng_empty_results_with_unresponsive_engines",
                message=(
                    "SearXNG returned no results and reported unresponsive engines: "
                    "brave, duckduckgo."
                ),
                status_code=200,
                content_type="application/json",
                body_preview=None,
                unresponsive_engines=["brave", "duckduckgo"],
            )
        results: tuple[SearchResultItem, ...] = ()
        if request.query_text == "LangGraph official docs overview":
            results = (
                SearchResultItem(
                    url="https://docs.langchain.com/oss/python/langgraph/overview",
                    title="LangGraph overview - Docs by LangChain",
                    snippet="Official docs",
                    source_engine="fake",
                    rank=1,
                ),
            )
        return SearchResponse(
            provider=self.name,
            source_engines=("fake",) if results else (),
            result_count=len(results),
            results=results,
            metadata={"request_query": request.query_text},
        )
