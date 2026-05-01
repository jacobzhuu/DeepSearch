from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from packages.db.repositories import ResearchTaskRepository, TaskEventRepository
from packages.observability import get_logger
from services.orchestrator.app.db import get_session_factory
from services.orchestrator.app.services.debug_pipeline import STATUS_FAILED
from services.orchestrator.app.services.pipeline_runtime import (
    PipelineConfigurationError,
    create_pipeline_runner,
    pipeline_dependency_summary,
    pipeline_running_mode,
)
from services.orchestrator.app.services.research_tasks import (
    RUNTIME_ACTIVE_STATUS_VALUES,
    RUNTIME_QUEUED_STATUS,
    build_task_event_payload,
)
from services.orchestrator.app.settings import Settings, get_settings

logger = get_logger(__name__)

RECOVERABLE_WORKER_STATUSES = tuple(
    status for status in RUNTIME_ACTIVE_STATUS_VALUES if status != RUNTIME_QUEUED_STATUS
)


class PipelineRunner(Protocol):
    def run(self, task_id: UUID) -> Any: ...


RunnerFactory = Callable[[Session], PipelineRunner]


class ResearchPipelineWorker:
    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        settings: Settings,
        runner_factory: RunnerFactory | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.settings = settings
        self.runner_factory = runner_factory or (
            lambda session: create_pipeline_runner(session, settings=settings)
        )

    def recover_interrupted_tasks(self) -> int:
        with self.session_factory() as session:
            task_repository = ResearchTaskRepository(session)
            event_repository = TaskEventRepository(session)
            tasks = task_repository.list_by_statuses(
                RECOVERABLE_WORKER_STATUSES,
                oldest_first=True,
            )
            for task in tasks:
                from_status = task.status
                task_repository.set_status(task, RUNTIME_QUEUED_STATUS, ended_at=None)
                event_repository.record(
                    task_id=task.id,
                    event_type="pipeline.requeued",
                    payload_json={
                        **build_task_event_payload(
                            from_status=from_status,
                            to_status=RUNTIME_QUEUED_STATUS,
                            changes={"revision_no": task.revision_no},
                        ),
                        "source": "pipeline.worker",
                        "stage": RUNTIME_QUEUED_STATUS,
                        "reason": "worker_startup_recovery",
                    },
                )
            session.commit()
            return len(tasks)

    def run_once(self) -> int:
        processed = 0
        with self.session_factory() as session:
            task_repository = ResearchTaskRepository(session)
            queued_tasks = task_repository.list_by_statuses(
                (RUNTIME_QUEUED_STATUS,),
                limit=max(1, self.settings.research_worker_batch_size),
                oldest_first=True,
            )
            task_ids = [task.id for task in queued_tasks]

        for task_id in task_ids:
            processed += self._run_task(task_id)
        return processed

    def run_forever(self) -> None:
        recovered = self.recover_interrupted_tasks()
        if recovered:
            logger.info("pipeline.worker.recovered", extra={"task_count": recovered})
        while True:
            processed = self.run_once()
            if processed <= 0:
                time.sleep(max(0.1, self.settings.research_worker_poll_interval_seconds))

    def _run_task(self, task_id: UUID) -> int:
        with self.session_factory() as session:
            task_repository = ResearchTaskRepository(session)
            task = task_repository.get(task_id)
            if task is None or task.status != RUNTIME_QUEUED_STATUS:
                return 0
            try:
                runner = self.runner_factory(session)
                result = runner.run(task_id)
            except PipelineConfigurationError as error:
                self._record_worker_failure(
                    session,
                    task_id,
                    reason="missing_configuration",
                    exception=type(error).__name__,
                    message=str(error),
                    details=error.to_payload(),
                )
            except Exception as error:  # noqa: BLE001 - worker must surface unexpected crashes.
                self._record_worker_failure(
                    session,
                    task_id,
                    reason="worker_unhandled_exception",
                    exception=type(error).__name__,
                    message=str(error),
                    details=None,
                )
            else:
                logger.info(
                    "pipeline.worker.task.completed",
                    extra={
                        "task_id": str(task_id),
                        "completed": result.completed,
                        "status": result.task.status,
                    },
                )
            return 1

    def _record_worker_failure(
        self,
        session: Session,
        task_id: UUID,
        *,
        reason: str,
        exception: str,
        message: str,
        details: dict[str, Any] | None,
    ) -> None:
        task_repository = ResearchTaskRepository(session)
        event_repository = TaskEventRepository(session)
        task = task_repository.get(task_id)
        if task is None:
            return
        from_status = task.status
        task_repository.set_status(task, STATUS_FAILED, ended_at=datetime.now(UTC))
        dependencies = pipeline_dependency_summary(self.settings)
        event_repository.record(
            task_id=task.id,
            event_type="pipeline.failed",
            payload_json={
                **build_task_event_payload(from_status=from_status, to_status=STATUS_FAILED),
                "source": "pipeline.worker",
                "stage": "WORKER",
                "reason": reason,
                "exception": exception,
                "message": message,
                "next_action": (
                    "Inspect worker logs and task events before retrying with a new task."
                ),
                "dependencies": dependencies,
                "running_mode": pipeline_running_mode(dependencies),
                "details": details,
            },
        )
        session.commit()


def run_worker_forever() -> None:
    ResearchPipelineWorker(
        session_factory=get_session_factory(),
        settings=get_settings(),
    ).run_forever()
