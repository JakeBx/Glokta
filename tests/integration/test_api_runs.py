"""Runs API integration tests."""

import uuid
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from garakboard.models import Model, Run


def test_create_run(api_client: TestClient, db_session: Session):
    """POST /api/runs creates a run with status=pending."""
    # Create a model first
    model = Model(
        name="test/model:free",
        provider="test-provider",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.commit()

    response = api_client.post("/api/runs", json={"model_id": str(model.id)})
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "pending"
    assert data["triggered_by"] == "api"
    assert data["model_id"] == str(model.id)


def test_create_run_requires_model_id(api_client: TestClient):
    """POST /api/runs without model_id returns 422."""
    response = api_client.post("/api/runs", json={})
    assert response.status_code == 422


def test_create_run_invalid_model_id_returns_404(api_client: TestClient):
    """POST /api/runs with non-existent model_id returns 404."""
    fake_id = uuid.uuid4()
    response = api_client.post("/api/runs", json={"model_id": str(fake_id)})
    assert response.status_code == 404
    assert response.json()["detail"] == "Model not found"


def test_list_runs_empty(api_client: TestClient):
    """GET /api/runs returns empty list when no runs exist."""
    response = api_client.get("/api/runs")
    assert response.status_code == 200
    assert response.json() == []


def test_list_runs_returns_created_run(api_client: TestClient, db_session: Session):
    """GET /api/runs returns runs after creation."""
    # Create a model and run
    model = Model(
        name="test/model:free",
        provider="test-provider",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    run = Run(
        model_id=model.id,
        triggered_by="api",
        status="pending",
    )
    db_session.add(run)
    db_session.commit()

    response = api_client.get("/api/runs")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["status"] == "pending"


def test_list_runs_filter_by_status(api_client: TestClient, db_session: Session):
    """GET /api/runs?status=pending returns only pending runs."""
    # Create a model
    model = Model(
        name="test/model:free",
        provider="test-provider",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    # Create pending and complete runs
    pending_run = Run(model_id=model.id, triggered_by="api", status="pending")
    complete_run = Run(model_id=model.id, triggered_by="api", status="complete")
    db_session.add(pending_run)
    db_session.add(complete_run)
    db_session.commit()

    response = api_client.get("/api/runs?status=pending")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["status"] == "pending"


def test_get_run_by_id(api_client: TestClient, db_session: Session):
    """GET /api/runs/{id} returns the correct run."""
    # Create a model and run
    model = Model(
        name="test/model:free",
        provider="test-provider",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    run = Run(model_id=model.id, triggered_by="api", status="pending")
    db_session.add(run)
    db_session.commit()

    response = api_client.get(f"/api/runs/{run.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(run.id)
    assert data["status"] == "pending"


def test_get_run_not_found(api_client: TestClient):
    """GET /api/runs/{unknown_id} returns 404."""
    unknown_id = uuid.uuid4()
    response = api_client.get(f"/api/runs/{unknown_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Run not found"