"""Community run submission and verification flow integration tests."""

import hashlib
import io
import uuid
from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from garakboard.models import Model, Run

SAMPLE_CONFIG_YAML = b"plugins:\n  target_name: test/model:free\n"
SAMPLE_JSONL = (
    b'{"entry_type":"eval","probe":"encoding.antml","detector":"base.Always",'
    b'"passed":5,"fails":2,"score":0.71}\n'
)


def _expected_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _post_community_run(
    api_client: TestClient,
    model_name: str = "openrouter/test/model:free",
    garak_version: str = "0.14.0",
    scanned_at: str = "2026-04-28T00:00:00Z",
    submitted_by: str | None = None,
    config_bytes: bytes = SAMPLE_CONFIG_YAML,
    jsonl_bytes: bytes = SAMPLE_JSONL,
):
    data: dict = {
        "model_name": model_name,
        "garak_version": garak_version,
        "scanned_at": scanned_at,
    }
    if submitted_by is not None:
        data["submitted_by"] = submitted_by

    return api_client.post(
        "/api/runs/community",
        data=data,
        files={
            "config_file": ("config.yaml", io.BytesIO(config_bytes), "application/yaml"),
            "jsonl_file": ("results.jsonl", io.BytesIO(jsonl_bytes), "application/octet-stream"),
        },
    )


# ── Community submission ─────────────────────────────────────────────────────


def test_community_run_submit_returns_201(api_client: TestClient):
    response = _post_community_run(api_client)
    assert response.status_code == 201


def test_community_run_submit_origin(api_client: TestClient):
    data = _post_community_run(api_client).json()
    assert data["triggered_by"] == "community"


def test_community_run_submit_status_complete(api_client: TestClient):
    data = _post_community_run(api_client).json()
    assert data["status"] == "complete"


def test_community_run_submit_garak_version(api_client: TestClient):
    data = _post_community_run(api_client, garak_version="0.14.1").json()
    assert data["garak_version"] == "0.14.1"


def test_community_run_submit_config_stored(api_client: TestClient):
    data = _post_community_run(api_client).json()
    assert data["garak_config"] == SAMPLE_CONFIG_YAML.decode()


def test_community_run_submit_config_hash(api_client: TestClient):
    data = _post_community_run(api_client).json()
    assert data["config_hash"] == _expected_hash(SAMPLE_CONFIG_YAML)


def test_community_run_submit_jsonl_hash(api_client: TestClient):
    data = _post_community_run(api_client).json()
    assert data["jsonl_manifest_hash"] == _expected_hash(SAMPLE_JSONL)


def test_community_run_submit_submitted_by(api_client: TestClient):
    data = _post_community_run(api_client, submitted_by="researcher@example.com").json()
    assert data["submitted_by"] == "researcher@example.com"


def test_community_run_submit_creates_model(api_client: TestClient, db_session: Session):
    _post_community_run(api_client, model_name="openrouter/new/model:free")
    model = db_session.query(Model).filter(Model.name == "openrouter/new/model:free").first()
    assert model is not None


def test_community_run_submit_idempotent_model(api_client: TestClient, db_session: Session):
    _post_community_run(api_client, model_name="openrouter/dupe/model:free")
    _post_community_run(api_client, model_name="openrouter/dupe/model:free")
    count = db_session.query(Model).filter(Model.name == "openrouter/dupe/model:free").count()
    assert count == 1


def test_community_run_submit_probe_results_ingested(api_client: TestClient, db_session: Session):
    from garakboard.models import ProbeResult
    resp = _post_community_run(api_client)
    run_id = resp.json()["id"]
    results = db_session.query(ProbeResult).filter(ProbeResult.run_id == uuid.UUID(run_id)).all()
    assert len(results) == 1
    assert results[0].probe_name == "encoding.antml"


def test_community_run_submit_missing_model_name(api_client: TestClient):
    resp = api_client.post(
        "/api/runs/community",
        data={"garak_version": "0.14.0", "scanned_at": "2026-04-28T00:00:00Z"},
        files={
            "config_file": ("c.yaml", io.BytesIO(SAMPLE_CONFIG_YAML), "application/yaml"),
            "jsonl_file": ("r.jsonl", io.BytesIO(SAMPLE_JSONL), "application/octet-stream"),
        },
    )
    assert resp.status_code == 422


# ── Verification request ─────────────────────────────────────────────────────


def test_request_verification_sets_timestamp(api_client: TestClient, db_session: Session):
    run_id = _post_community_run(api_client).json()["id"]
    resp = api_client.post(f"/api/runs/{run_id}/request-verification")
    assert resp.status_code == 200
    data = resp.json()
    assert data["verification_requested_at"] is not None


def test_request_verification_idempotent_returns_409(api_client: TestClient):
    run_id = _post_community_run(api_client).json()["id"]
    api_client.post(f"/api/runs/{run_id}/request-verification")
    resp = api_client.post(f"/api/runs/{run_id}/request-verification")
    assert resp.status_code == 409


def test_request_verification_on_api_run_returns_400(api_client: TestClient, db_session: Session):
    model = Model(name="openrouter/api/model:free", provider="test", snapshot_date=date.today())
    db_session.add(model)
    db_session.flush()
    run = Run(model_id=model.id, triggered_by="api", status="complete")
    db_session.add(run)
    db_session.commit()

    resp = api_client.post(f"/api/runs/{run.id}/request-verification")
    assert resp.status_code == 400


def test_request_verification_nonexistent_run_returns_404(api_client: TestClient):
    resp = api_client.post(f"/api/runs/{uuid.uuid4()}/request-verification")
    assert resp.status_code == 404


# ── Admin verify trigger ─────────────────────────────────────────────────────


def test_verify_creates_verified_run(api_client: TestClient, db_session: Session):
    run_id = _post_community_run(api_client).json()["id"]
    api_client.post(f"/api/runs/{run_id}/request-verification")

    resp = api_client.post(f"/api/runs/{run_id}/verify")
    assert resp.status_code == 201
    verified = resp.json()
    assert verified["triggered_by"] == "verified"
    assert verified["source_community_run_id"] == run_id


def test_verify_links_back_on_community_run_get(api_client: TestClient):
    run_id = _post_community_run(api_client).json()["id"]
    api_client.post(f"/api/runs/{run_id}/request-verification")
    verified_id = api_client.post(f"/api/runs/{run_id}/verify").json()["id"]

    community_resp = api_client.get(f"/api/runs/{run_id}").json()
    assert community_resp["verified_run_id"] == verified_id


def test_verify_requires_verification_request(api_client: TestClient):
    run_id = _post_community_run(api_client).json()["id"]
    resp = api_client.post(f"/api/runs/{run_id}/verify")
    assert resp.status_code == 400


def test_verify_non_community_run_returns_400(api_client: TestClient, db_session: Session):
    model = Model(name="openrouter/sched/model:free", provider="test", snapshot_date=date.today())
    db_session.add(model)
    db_session.flush()
    run = Run(model_id=model.id, triggered_by="scheduled", status="complete")
    db_session.add(run)
    db_session.commit()

    resp = api_client.post(f"/api/runs/{run.id}/verify")
    assert resp.status_code == 400


def test_verify_nonexistent_run_returns_404(api_client: TestClient):
    resp = api_client.post(f"/api/runs/{uuid.uuid4()}/verify")
    assert resp.status_code == 404


# ── Verification filter in list ──────────────────────────────────────────────


def test_list_runs_filter_verification_requested(api_client: TestClient):
    run_id = _post_community_run(api_client).json()["id"]
    api_client.post(f"/api/runs/{run_id}/request-verification")

    resp = api_client.get("/api/runs?verification_requested=true")
    assert resp.status_code == 200
    ids = [r["id"] for r in resp.json()]
    assert run_id in ids
