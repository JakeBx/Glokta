"""Runs API router."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from garakboard.api.deps import get_db
from garakboard.models import Model, Run
from garakboard.schemas import RunCreate, RunResponse, RunSummaryRow
from garakboard.worker.tasks import publish_run_job

router = APIRouter()


@router.post("/runs", response_model=RunResponse, status_code=201)
def create_run(run_data: RunCreate, db: Session = Depends(get_db)) -> Run:
    """Create a run record with status='pending', triggered_by='api'; publish job to Redis queue."""
    # Verify model exists
    model = db.query(Model).filter(Model.id == run_data.model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    # Create run record
    run = Run(
        model_id=run_data.model_id,
        triggered_by="api",
        status="pending",
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    # Publish job to Celery queue
    publish_run_job(str(run.id), model.name, run_data.probe_categories)

    return run


@router.get("/runs", response_model=list[RunResponse])
def list_runs(status: str | None = None, db: Session = Depends(get_db)) -> list[Run]:
    """List runs; optional query param status; ordered by created_at desc."""
    query = db.query(Run).order_by(Run.created_at.desc())
    if status is not None:
        query = query.filter(Run.status == status)
    return query.all()


@router.get("/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: UUID, db: Session = Depends(get_db)) -> Run:
    """Get a single run by UUID; 404 if not found."""
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/runs/summary/by-model", response_model=list[RunSummaryRow])
def get_runs_summary(db: Session = Depends(get_db)) -> list[RunSummaryRow]:
    """Per-model run status counts: pending, running, complete, failed."""
    rows = db.execute(
        select(
            Model.name.label("model_name"),
            Model.provider.label("provider"),
            func.count(Run.id).filter(Run.status == "pending").label("pending"),
            func.count(Run.id).filter(Run.status == "running").label("running"),
            func.count(Run.id).filter(Run.status == "complete").label("complete"),
            func.count(Run.id).filter(Run.status == "failed").label("failed"),
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
        )
        for r in rows
    ]