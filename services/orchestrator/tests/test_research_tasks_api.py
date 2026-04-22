from __future__ import annotations

from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.main import create_app


@pytest.fixture()
def client(session_factory: sessionmaker[Session]) -> Generator[TestClient, None, None]:
    app = create_app()

    def override_db_session() -> Generator[Session, None, None]:
        with session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()


def test_create_task_and_fetch_detail_and_events(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/research/tasks",
        json={
            "query": "NVIDIA open model ecosystem updates",
            "constraints": {"language": "zh-CN"},
        },
    )

    assert create_response.status_code == 201
    payload = create_response.json()
    task_id = payload["task_id"]
    assert payload["status"] == "PLANNED"

    detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
    events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

    assert detail_response.status_code == 200
    assert detail_response.json()["progress"]["events_total"] == 1
    assert detail_response.json()["constraints"] == {"language": "zh-CN"}

    assert events_response.status_code == 200
    assert events_response.json()["events"][0]["event_type"] == "task.created"
    assert events_response.json()["events"][0]["payload"]["to_status"] == "PLANNED"


def test_pause_resume_and_cancel_endpoints_change_status(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/research/tasks",
        json={"query": "Lifecycle task", "constraints": {}},
    )
    task_id = create_response.json()["task_id"]

    pause_response = client.post(f"/api/v1/research/tasks/{task_id}/pause")
    resume_response = client.post(f"/api/v1/research/tasks/{task_id}/resume")
    cancel_response = client.post(f"/api/v1/research/tasks/{task_id}/cancel")
    detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
    events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

    assert pause_response.status_code == 200
    assert pause_response.json()["status"] == "PAUSED"
    assert resume_response.status_code == 200
    assert resume_response.json()["status"] == "PLANNED"
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "CANCELLED"
    assert detail_response.json()["ended_at"] is not None
    assert [event["event_type"] for event in events_response.json()["events"]] == [
        "task.created",
        "task.paused",
        "task.resumed",
        "task.cancelled",
    ]


def test_revise_endpoint_updates_query_and_constraints(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/research/tasks",
        json={"query": "Original task", "constraints": {"language": "en"}},
    )
    task_id = create_response.json()["task_id"]

    client.post(f"/api/v1/research/tasks/{task_id}/pause")
    revise_response = client.post(
        f"/api/v1/research/tasks/{task_id}/revise",
        json={"query": "Revised task", "constraints": {"max_rounds": 2}},
    )
    detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
    events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

    assert revise_response.status_code == 200
    assert revise_response.json()["status"] == "PLANNED"
    assert detail_response.json()["query"] == "Revised task"
    assert detail_response.json()["constraints"] == {"language": "en", "max_rounds": 2}
    assert events_response.json()["events"][-1]["event_type"] == "task.revised"
    assert events_response.json()["events"][-1]["payload"]["from_status"] == "PAUSED"


def test_invalid_transition_and_not_found_responses(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/research/tasks",
        json={"query": "Transition guard task", "constraints": {}},
    )
    task_id = create_response.json()["task_id"]

    invalid_resume_response = client.post(f"/api/v1/research/tasks/{task_id}/resume")
    not_found_response = client.get(f"/api/v1/research/tasks/{uuid4()}")

    assert invalid_resume_response.status_code == 409
    assert "cannot resume task" in invalid_resume_response.json()["detail"]
    assert not_found_response.status_code == 404
