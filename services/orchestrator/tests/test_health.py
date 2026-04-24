from fastapi.testclient import TestClient

from services.orchestrator.app.main import app


def test_healthz_returns_ok() -> None:
    response = TestClient(app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_returns_service_metadata() -> None:
    response = TestClient(app).get("/readyz")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    assert response.json()["service"] == "deepresearch-orchestrator"


def test_metrics_returns_prometheus_payload() -> None:
    client = TestClient(app)
    client.get("/healthz")

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "deepresearch_http_requests_total" in response.text
