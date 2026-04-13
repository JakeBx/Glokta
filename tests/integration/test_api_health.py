"""Health endpoint integration tests."""

from fastapi.testclient import TestClient


def test_health_returns_ok(api_client: TestClient):
    """GET /api/health returns 200 with status ok."""
    response = api_client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}