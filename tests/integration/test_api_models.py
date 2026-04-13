"""Models API integration tests."""

import uuid
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from garakboard.models import Model


def test_list_models_empty(api_client: TestClient):
    """GET /api/models returns empty list when no models exist."""
    response = api_client.get("/api/models")
    assert response.status_code == 200
    assert response.json() == []


def test_list_models_returns_created_model(api_client: TestClient, db_session: Session):
    """GET /api/models returns created model."""
    # Create a model directly
    model = Model(
        name="test/model:free",
        provider="test-provider",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.commit()

    response = api_client.get("/api/models")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "test/model:free"
    assert data[0]["provider"] == "test-provider"


def test_get_model_by_id(api_client: TestClient, db_session: Session):
    """GET /api/models/{id} returns correct model."""
    model = Model(
        name="test/model:free",
        provider="test-provider",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.commit()

    response = api_client.get(f"/api/models/{model.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "test/model:free"
    assert data["provider"] == "test-provider"


def test_get_model_not_found(api_client: TestClient):
    """GET /api/models/{unknown_id} returns 404."""
    unknown_id = uuid.uuid4()
    response = api_client.get(f"/api/models/{unknown_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Model not found"