from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from packages.db.repositories import ResearchTaskRepository, TaskEventRepository
from services.orchestrator.app.services.pipeline_worker import ResearchPipelineWorker
from services.orchestrator.app.services.research_tasks import (
    PHASE2_ACTIVE_STATUS,
    RUNTIME_QUEUED_STATUS,
    build_task_event_payload,
    create_research_task_service,
)
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
