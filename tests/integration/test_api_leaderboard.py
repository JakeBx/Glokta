"""Leaderboard API integration tests."""

import uuid
from datetime import date

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from garakboard.models import Model, Run, ProbeResult


def test_leaderboard_empty(api_client: TestClient):
    """GET /api/leaderboard returns empty rows when no complete runs exist."""
    response = api_client.get("/api/leaderboard")
    assert response.status_code == 200
    data = response.json()
    assert data["rows"] == []
    assert data["total"] == 0
    assert data["page"] == 1


def test_leaderboard_returns_aggregated_scores(api_client: TestClient, db_session: Session):
    """GET /api/leaderboard returns correct aggregated scores for complete runs."""
    # Create model
    model = Model(
        name="test/model:free",
        provider="test-provider",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    # Create complete run
    run = Run(
        model_id=model.id,
        triggered_by="api",
        status="complete",
    )
    db_session.add(run)
    db_session.flush()

    # Create probe results
    probe1 = ProbeResult(
        run_id=run.id,
        probe_name="encoding.InjectBase64",
        probe_category="encoding",
        detector="always.Fail",
        pass_count=8,
        fail_count=2,
        score=0.8,
    )
    probe2 = ProbeResult(
        run_id=run.id,
        probe_name="prompt_injection.Simple",
        probe_category="injection",
        detector="always.Fail",
        pass_count=5,
        fail_count=5,
        score=0.5,
    )
    db_session.add(probe1)
    db_session.add(probe2)
    db_session.commit()

    response = api_client.get("/api/leaderboard")
    assert response.status_code == 200
    data = response.json()
    assert len(data["rows"]) == 2  # One row per probe_category

    # Find the encoding row
    encoding_row = next((r for r in data["rows"] if r["probe_category"] == "encoding"), None)
    assert encoding_row is not None
    assert encoding_row["total_pass"] == 8
    assert encoding_row["total_fail"] == 2
    assert encoding_row["score"] == 0.8


def test_leaderboard_filter_by_probe_category(api_client: TestClient, db_session: Session):
    """GET /api/leaderboard?probe_category=encoding filters correctly."""
    # Create model
    model = Model(
        name="test/model:free",
        provider="test-provider",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    # Create complete run
    run = Run(
        model_id=model.id,
        triggered_by="api",
        status="complete",
    )
    db_session.add(run)
    db_session.flush()

    # Create probe results in different categories
    encoding_probe = ProbeResult(
        run_id=run.id,
        probe_name="encoding.InjectBase64",
        probe_category="encoding",
        detector="always.Fail",
        pass_count=8,
        fail_count=2,
        score=0.8,
    )
    injection_probe = ProbeResult(
        run_id=run.id,
        probe_name="prompt_injection.Simple",
        probe_category="injection",
        detector="always.Fail",
        pass_count=5,
        fail_count=5,
        score=0.5,
    )
    db_session.add(encoding_probe)
    db_session.add(injection_probe)
    db_session.commit()

    response = api_client.get("/api/leaderboard?probe_category=encoding")
    assert response.status_code == 200
    data = response.json()
    assert len(data["rows"]) == 1
    assert data["rows"][0]["probe_category"] == "encoding"


def test_leaderboard_filter_by_model_id(api_client: TestClient, db_session: Session):
    """GET /api/leaderboard?model_id={id} filters correctly."""
    # Create two models
    model1 = Model(name="model/one", provider="provider1", snapshot_date=date.today())
    model2 = Model(name="model/two", provider="provider2", snapshot_date=date.today())
    db_session.add(model1)
    db_session.add(model2)
    db_session.flush()

    # Create complete runs
    run1 = Run(model_id=model1.id, triggered_by="api", status="complete")
    run2 = Run(model_id=model2.id, triggered_by="api", status="complete")
    db_session.add(run1)
    db_session.add(run2)
    db_session.flush()

    # Create probe results
    pr1 = ProbeResult(
        run_id=run1.id,
        probe_name="encoding.Test",
        probe_category="encoding",
        detector="always.Fail",
        pass_count=8,
        fail_count=2,
        score=0.8,
    )
    pr2 = ProbeResult(
        run_id=run2.id,
        probe_name="encoding.Test",
        probe_category="encoding",
        detector="always.Fail",
        pass_count=3,
        fail_count=7,
        score=0.3,
    )
    db_session.add(pr1)
    db_session.add(pr2)
    db_session.commit()

    response = api_client.get(f"/api/leaderboard?model_id={model1.id}")
    assert response.status_code == 200
    data = response.json()
    assert len(data["rows"]) == 1
    assert data["rows"][0]["score"] == 0.8


def test_leaderboard_pagination(api_client: TestClient, db_session: Session):
    """GET /api/leaderboard?page=1&page_size=1 returns one row."""
    # Create model
    model = Model(
        name="test/model:free",
        provider="test-provider",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    # Create complete run
    run = Run(
        model_id=model.id,
        triggered_by="api",
        status="complete",
    )
    db_session.add(run)
    db_session.flush()

    # Create probe results in different categories
    for cat in ["encoding", "injection", "xss"]:
        pr = ProbeResult(
            run_id=run.id,
            probe_name=f"{cat}.Test",
            probe_category=cat,
            detector="always.Fail",
            pass_count=5,
            fail_count=5,
            score=0.5,
        )
        db_session.add(pr)
    db_session.commit()

    response = api_client.get("/api/leaderboard?page=1&page_size=1")
    assert response.status_code == 200
    data = response.json()
    assert len(data["rows"]) == 1
    assert data["total"] == 3
    assert data["page"] == 1
    assert data["page_size"] == 1
    assert data["total_pages"] == 3


def test_leaderboard_model_detail(api_client: TestClient, db_session: Session):
    """GET /api/leaderboard/{model_id} returns per-model probe breakdown."""
    # Create model
    model = Model(
        name="test/model:free",
        provider="test-provider",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    # Create complete run
    run = Run(
        model_id=model.id,
        triggered_by="api",
        status="complete",
    )
    db_session.add(run)
    db_session.flush()

    # Create probe results
    probe1 = ProbeResult(
        run_id=run.id,
        probe_name="encoding.InjectBase64",
        probe_category="encoding",
        detector="always.Fail",
        pass_count=8,
        fail_count=2,
        score=0.8,
    )
    probe2 = ProbeResult(
        run_id=run.id,
        probe_name="prompt_injection.Simple",
        probe_category="injection",
        detector="always.Fail",
        pass_count=5,
        fail_count=5,
        score=0.5,
    )
    db_session.add(probe1)
    db_session.add(probe2)
    db_session.commit()

    response = api_client.get(f"/api/leaderboard/{model.id}")
    assert response.status_code == 200
    data = response.json()
    assert data["model_name"] == "test/model:free"
    assert len(data["probe_results"]) == 2
    assert data["summary"] is not None
    assert data["summary"]["total_pass"] == 13
    assert data["summary"]["total_fail"] == 7


def test_leaderboard_model_detail_not_found(api_client: TestClient):
    """GET /api/leaderboard/{unknown_id} returns 404."""
    unknown_id = uuid.uuid4()
    response = api_client.get(f"/api/leaderboard/{unknown_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "Model not found"


def test_leaderboard_excludes_non_complete_runs(api_client: TestClient, db_session: Session):
    """Runs with status != 'complete' are excluded from leaderboard."""
    # Create model
    model = Model(
        name="test/model:free",
        provider="test-provider",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    # Create pending run (should be excluded)
    pending_run = Run(
        model_id=model.id,
        triggered_by="api",
        status="pending",
    )
    db_session.add(pending_run)
    db_session.flush()

    # Create probe result for pending run
    pr = ProbeResult(
        run_id=pending_run.id,
        probe_name="encoding.Test",
        probe_category="encoding",
        detector="always.Fail",
        pass_count=10,
        fail_count=0,
        score=1.0,
    )
    db_session.add(pr)
    db_session.commit()

    # Leaderboard should be empty (pending run excluded)
    response = api_client.get("/api/leaderboard")
    assert response.status_code == 200
    data = response.json()
    assert data["rows"] == []
    assert data["total"] == 0