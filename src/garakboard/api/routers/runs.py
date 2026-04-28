"""Runs API router."""

import hashlib
import tempfile
import os
from datetime import date, datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from garakboard.api.deps import get_db
from garakboard.ingest.jsonl_parser import ingest_jsonl_file
from garakboard.models import Model, Run
from garakboard.schemas import RunCreate, RunResponse, RunSummaryRow
from garakboard.worker.tasks import publish_run_job

router = APIRouter()


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _upsert_model(db: Session, model_name: str) -> Model:
    """Return existing Model by name, creating it if absent."""
    model = db.query(Model).filter(Model.name == model_name).first()
    if model is None:
        model = Model(
            name=model_name,
            provider=model_name.split("/")[1] if "/" in model_name else model_name,
            snapshot_date=date.today(),
        )
        db.add(model)
        db.flush()
    return model


def _build_run_response(run: Run, db: Session) -> RunResponse:
    """Build RunResponse, populating verified_run_id for community runs."""
    response = RunResponse.model_validate(run)
    if run.triggered_by == "community":
        verified = (
            db.query(Run)
            .filter(
                Run.source_community_run_id == run.id,
                Run.triggered_by == "verified",
            )
            .first()
        )
        if verified:
            response.verified_run_id = verified.id
    return response


@router.post("/runs", response_model=RunResponse, status_code=201)
def create_run(run_data: RunCreate, db: Session = Depends(get_db)) -> RunResponse:
    """Create a run record with status='pending', triggered_by='api'; publish job to Redis queue."""
    model = db.query(Model).filter(Model.id == run_data.model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    run = Run(
        model_id=run_data.model_id,
        triggered_by="api",
        status="pending",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    publish_run_job(str(run.id), model.name, run_data.probe_categories)

    return RunResponse.model_validate(run)


@router.post("/runs/community", response_model=RunResponse, status_code=201)
async def submit_community_run(
    model_name: str = Form(...),
    garak_version: str = Form(...),
    scanned_at: datetime = Form(...),
    submitted_by: str | None = Form(default=None),
    config_file: UploadFile = File(...),
    jsonl_file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> RunResponse:
    """Accept a community-contributed garak scan result with full reproducibility metadata."""
    config_bytes = await config_file.read()
    jsonl_bytes = await jsonl_file.read()

    model = _upsert_model(db, model_name)

    run = Run(
        model_id=model.id,
        triggered_by="community",
        status="complete",
        garak_version=garak_version,
        scanned_at=scanned_at,
        submitted_by=submitted_by,
        garak_config=config_bytes.decode("utf-8", errors="replace"),
        config_hash=_sha256_hex(config_bytes),
        jsonl_manifest_hash=_sha256_hex(jsonl_bytes),
        completed_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    with tempfile.NamedTemporaryFile(mode="wb", suffix=".jsonl", delete=False) as tmp:
        tmp.write(jsonl_bytes)
        tmp_path = tmp.name

    try:
        ingest_jsonl_file(tmp_path, str(run.id), db)
    finally:
        os.unlink(tmp_path)

    db.commit()
    db.refresh(run)

    return _build_run_response(run, db)


@router.get("/runs", response_model=list[RunResponse])
def list_runs(
    status: str | None = None,
    verification_requested: bool | None = None,
    db: Session = Depends(get_db),
) -> list[RunResponse]:
    """List runs; optional query params status and verification_requested; ordered by created_at desc."""
    query = db.query(Run).order_by(Run.created_at.desc())
    if status is not None:
        query = query.filter(Run.status == status)
    if verification_requested is True:
        query = query.filter(Run.verification_requested_at.isnot(None))
    runs = query.all()
    return [RunResponse.model_validate(r) for r in runs]


@router.get("/runs/summary/by-model", response_model=list[RunSummaryRow])
def get_runs_summary(db: Session = Depends(get_db)) -> list[RunSummaryRow]:
    """Per-model run status counts: pending, running, complete, failed."""
    latest_origin = (
        select(Run.triggered_by)
        .where(Run.model_id == Model.id)
        .order_by(Run.created_at.desc())
        .limit(1)
        .correlate(Model)
        .scalar_subquery()
    )

    rows = db.execute(
        select(
            Model.name.label("model_name"),
            Model.provider.label("provider"),
            func.count(Run.id).filter(Run.status == "pending").label("pending"),
            func.count(Run.id).filter(Run.status == "running").label("running"),
            func.count(Run.id).filter(Run.status == "complete").label("complete"),
            func.count(Run.id).filter(Run.status == "failed").label("failed"),
            latest_origin.label("latest_origin"),
        )
        .join(Run, Run.model_id == Model.id)
        .group_by(Model.id, Model.name, Model.provider)
        .order_by(Model.name)
    ).all()

    return [
        RunSummaryRow(
            model_name=r.model_name,
            provider=r.provider,
            pending=r.pending,
            running=r.running,
            complete=r.complete,
            failed=r.failed,
            latest_origin=r.latest_origin or "api",
        )
        for r in rows
    ]


@router.get("/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: UUID, db: Session = Depends(get_db)) -> RunResponse:
    """Get a single run by UUID; 404 if not found. Populates verified_run_id for community runs."""
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _build_run_response(run, db)


@router.post("/runs/{run_id}/request-verification", response_model=RunResponse)
def request_verification(run_id: UUID, db: Session = Depends(get_db)) -> RunResponse:
    """Mark a community run as requesting verification review."""
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.triggered_by != "community":
        raise HTTPException(status_code=400, detail="Only community runs can request verification")
    if run.verification_requested_at is not None:
        raise HTTPException(status_code=409, detail="Verification already requested")

    run.verification_requested_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(run)
    return _build_run_response(run, db)


@router.post("/runs/{run_id}/verify", response_model=RunResponse, status_code=201)
def trigger_verified_scan(run_id: UUID, db: Session = Depends(get_db)) -> RunResponse:
    """Create a verified re-scan of a community run that has requested verification."""
    community_run = db.query(Run).filter(Run.id == run_id).first()
    if not community_run:
        raise HTTPException(status_code=404, detail="Run not found")
    if community_run.triggered_by != "community":
        raise HTTPException(status_code=400, detail="Only community runs can be verified")
    if community_run.verification_requested_at is None:
        raise HTTPException(status_code=400, detail="Verification has not been requested for this run")

    from garakboard.models import ProbeResult
    probe_categories = list({
        r.probe_category
        for r in db.query(ProbeResult).filter(ProbeResult.run_id == run_id).all()
    })

    model = db.query(Model).filter(Model.id == community_run.model_id).first()

    verified_run = Run(
        model_id=community_run.model_id,
        triggered_by="verified",
        status="pending",
        source_community_run_id=run_id,
    )
    db.add(verified_run)
    db.commit()
    db.refresh(verified_run)

    publish_run_job(str(verified_run.id), model.name, probe_categories)

    return RunResponse.model_validate(verified_run)
