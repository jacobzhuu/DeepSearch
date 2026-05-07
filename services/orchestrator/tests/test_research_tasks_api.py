from __future__ import annotations

from collections.abc import Generator
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from services.orchestrator.app.db import get_db_session
from services.orchestrator.app.main import create_app
from services.orchestrator.app.settings import get_settings


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
            "report_language": "zh-CN",
        },
    )

    assert create_response.status_code == 201
    payload = create_response.json()
    task_id = payload["task_id"]
    assert payload["status"] == "PLANNED"
    assert payload["revision_no"] == 1

    detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
    events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

    assert detail_response.status_code == 200
    assert detail_response.json()["progress"]["events_total"] == 1
    assert detail_response.json()["constraints"] == {
        "language": "zh-CN",
        "report_language": "zh-CN",
    }
    assert detail_response.json()["revision_no"] == 1

    assert events_response.status_code == 200
    assert events_response.json()["events"][0]["event_type"] == "task.created"
    assert events_response.json()["events"][0]["sequence_no"] == 1
    assert events_response.json()["events"][0]["payload"]["to_status"] == "PLANNED"


def test_list_tasks_returns_recent_task_summaries(client: TestClient) -> None:
    first_response = client.post(
        "/api/v1/research/tasks",
        json={"query": "First list task", "constraints": {}},
    )
    second_response = client.post(
        "/api/v1/research/tasks",
        json={"query": "Second list task", "constraints": {}},
    )

    list_response = client.get("/api/v1/research/tasks")
    planned_response = client.get("/api/v1/research/tasks?status=planned&limit=1")

    assert first_response.status_code == 201
    assert second_response.status_code == 201
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["count"] >= 2
    task_ids = [task["task_id"] for task in list_payload["tasks"]]
    assert second_response.json()["task_id"] in task_ids
    assert first_response.json()["task_id"] in task_ids
    assert list_payload["tasks"][0]["events_total"] == 1
    assert list_payload["tasks"][0]["latest_event_at"] is not None

    assert planned_response.status_code == 200
    planned_payload = planned_response.json()
    assert planned_payload["count"] == 1
    assert planned_payload["tasks"][0]["status"] == "PLANNED"


def test_create_task_top_level_report_language_updates_constraints_language(
    client: TestClient,
) -> None:
    create_response = client.post(
        "/api/v1/research/tasks",
        json={
            "query": "How to deploy SearXNG with Docker?",
            "report_language": "zh_CN",
        },
    )
    task_id = create_response.json()["task_id"]
    detail_response = client.get(f"/api/v1/research/tasks/{task_id}")

    assert create_response.status_code == 201
    assert detail_response.json()["constraints"] == {
        "language": "zh-CN",
        "report_language": "zh-CN",
    }


def test_plan_endpoint_records_visible_pre_run_research_plan(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "false")
    monkeypatch.setenv("RESEARCH_PLANNER_ENABLED", "false")
    get_settings.cache_clear()
    create_response = client.post(
        "/api/v1/research/tasks",
        json={"query": "What is SearXNG and how does it work?", "constraints": {}},
    )
    task_id = create_response.json()["task_id"]

    plan_response = client.post(f"/api/v1/research/tasks/{task_id}/plan")
    plan_read_response = client.get(f"/api/v1/research/tasks/{task_id}/plan")
    detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
    events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

    assert plan_response.status_code == 200
    plan_payload = plan_response.json()
    assert plan_payload["status"] == "PLANNED"
    assert plan_payload["planner_status"] == "created"
    assert plan_payload["planner_mode"] == "deterministic"
    assert plan_payload["plan_source"] == "deterministic_fallback"
    assert plan_payload["research_plan"]["search_queries"]
    assert plan_payload["running_mode"].endswith("+no-LLM")
    assert "No LLM planner is active; deterministic planner used." in plan_payload["warnings"]
    assert plan_read_response.status_code == 200
    assert plan_read_response.json()["research_plan"]["intent"] == "definition_how_it_works"
    assert plan_read_response.json()["planner_status"] == "created"

    detail_payload = detail_response.json()
    assert detail_payload["status"] == "PLANNED"
    assert detail_payload["progress"]["current_state"] == "PLANNING"
    observability = detail_payload["progress"]["observability"]
    assert observability["planner_status"] == "created"
    assert observability["plan_source"] == "deterministic_fallback"
    assert observability["research_plan"]["intent"] == "definition_how_it_works"
    assert observability["running_mode"].endswith("+no-LLM")
    assert "No LLM planner is active; deterministic planner used." in observability["warnings"]

    event_types = [event["event_type"] for event in events_response.json()["events"]]
    assert event_types == ["task.created", "research_plan.created"]
    get_settings.cache_clear()


def test_plan_endpoint_llm_provider_failure_records_deterministic_fallback(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("LLM_API_KEY", "test-api-key")
    monkeypatch.setenv("RESEARCH_PLANNER_ENABLED", "true")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    get_settings.cache_clear()
    create_response = client.post(
        "/api/v1/research/tasks",
        json={"query": "What is LangGraph and how does it work?", "constraints": {}},
    )
    task_id = create_response.json()["task_id"]

    plan_response = client.post(f"/api/v1/research/tasks/{task_id}/plan")
    detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
    events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

    assert plan_response.status_code == 200
    plan_payload = plan_response.json()
    assert plan_payload["planner_status"] == "fallback"
    assert plan_payload["planner_mode"] == "deterministic"
    assert plan_payload["plan_source"] == "deterministic_fallback_after_llm_failure"
    assert (
        "LLM planner failed validation/provider call; deterministic fallback was used."
        in plan_payload["warnings"]
    )
    assert "No LLM planner is active; deterministic planner used." not in plan_payload["warnings"]
    assert "test-api-key" not in str(events_response.json())

    observability = detail_response.json()["progress"]["observability"]
    assert observability["planner_status"] == "fallback"
    assert observability["plan_source"] == "deterministic_fallback_after_llm_failure"
    assert observability["research_plan"]["planner_diagnostics"]["planner_fallback"] is True
    assert (
        "LLM planner failed validation/provider call; deterministic fallback was used."
        in observability["warnings"]
    )
    assert "No LLM planner is active; deterministic planner used." not in observability["warnings"]
    get_settings.cache_clear()


def test_plan_endpoint_preserves_planner_diagnostics_after_operator_edit(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setenv("LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("LLM_API_KEY", "test-api-key")
    monkeypatch.setenv("RESEARCH_PLANNER_ENABLED", "true")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    get_settings.cache_clear()
    create_response = client.post(
        "/api/v1/research/tasks",
        json={"query": "What is LangGraph and how does it work?", "constraints": {}},
    )
    task_id = create_response.json()["task_id"]

    fallback_response = client.post(f"/api/v1/research/tasks/{task_id}/plan")
    edited_plan = dict(fallback_response.json()["research_plan"])
    edited_plan.pop("planner_diagnostics", None)
    edited_plan["subquestions"] = [*edited_plan["subquestions"], "What should the operator edit?"]

    edit_response = client.post(
        f"/api/v1/research/tasks/{task_id}/plan",
        json={"research_plan": edited_plan},
    )
    detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
    events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

    assert edit_response.status_code == 200
    assert edit_response.json()["plan_source"] == "operator_edited"
    diagnostics = detail_response.json()["progress"]["observability"]["research_plan"][
        "planner_diagnostics"
    ]
    assert diagnostics["planner_fallback"] is True
    assert diagnostics["fallback_reason"] == "llm_provider_failed"
    assert diagnostics["preserved_after_operator_edit"] is True
    created_events = [
        event
        for event in events_response.json()["events"]
        if event["event_type"] == "research_plan.created"
    ]
    assert (
        created_events[-1]["payload"]["result"]["research_plan"]["planner_diagnostics"][
            "preserved_after_operator_edit"
        ]
        is True
    )
    get_settings.cache_clear()


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
    assert resume_response.json()["status"] == "QUEUED"
    assert cancel_response.status_code == 200
    assert cancel_response.json()["status"] == "CANCELLED"
    assert detail_response.json()["ended_at"] is not None
    assert [event["sequence_no"] for event in events_response.json()["events"]] == [1, 2, 3, 4]
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
        json={
            "query": "Revised task",
            "constraints": {"max_rounds": 2},
            "report_language": "zh-CN",
        },
    )
    detail_response = client.get(f"/api/v1/research/tasks/{task_id}")
    events_response = client.get(f"/api/v1/research/tasks/{task_id}/events")

    assert revise_response.status_code == 200
    assert revise_response.json()["status"] == "PLANNED"
    assert revise_response.json()["revision_no"] == 2
    assert detail_response.json()["query"] == "Revised task"
    assert detail_response.json()["constraints"] == {
        "language": "en",
        "max_rounds": 2,
        "report_language": "zh-CN",
    }
    assert detail_response.json()["revision_no"] == 2
    assert events_response.json()["events"][-1]["event_type"] == "task.revised"
    assert events_response.json()["events"][-1]["payload"]["from_status"] == "PAUSED"
    assert events_response.json()["events"][-1]["payload"]["changes"]["revision_no"] == 2


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


def test_events_endpoint_supports_after_sequence_no_and_limit(client: TestClient) -> None:
    create_response = client.post(
        "/api/v1/research/tasks",
        json={"query": "Polling task", "constraints": {}},
    )
    task_id = create_response.json()["task_id"]

    client.post(f"/api/v1/research/tasks/{task_id}/pause")
    client.post(
        f"/api/v1/research/tasks/{task_id}/revise",
        json={"constraints": {"max_rounds": 2}},
    )
    client.post(f"/api/v1/research/tasks/{task_id}/pause")

    events_response = client.get(
        f"/api/v1/research/tasks/{task_id}/events",
        params={"after_sequence_no": 2, "limit": 2},
    )

    assert events_response.status_code == 200
    assert [event["sequence_no"] for event in events_response.json()["events"]] == [3, 4]
    assert [event["event_type"] for event in events_response.json()["events"]] == [
        "task.revised",
        "task.paused",
    ]
