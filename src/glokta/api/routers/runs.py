"""Runs API router — read-only endpoints for the frontend."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from glokta.api.deps import get_db
from glokta.infrastructure.db.orm import Attempt, Model, ProbeResult, Run
from glokta.infrastructure.db.repos import RunRepository
from glokta.api.schemas import AttemptResponse, ProbeResultResponse, RunResponse, RunSummaryRow

router = APIRouter()


@router.get("/runs", response_model=list[RunResponse])
def list_runs(
    status: str | None = None,
    db: Session = Depends(get_db),
) -> list[RunResponse]:
    """List runs; optional query param status; ordered by created_at desc."""
    runs = RunRepository(db).list_all(status=status)
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
            latest_origin=r.latest_origin or "scheduled",
        )
        for r in rows
    ]


@router.get("/runs/{run_id}/attempts", response_model=list[AttemptResponse])
def get_run_attempts(
    run_id: UUID,
    probe_name: str | None = None,
    db: Session = Depends(get_db),
) -> list[AttemptResponse]:
    """Return attempts for a run, optionally filtered by probe_name."""
    run = RunRepository(db).find_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    query = db.query(Attempt).filter(Attempt.run_id == run_id)
    if probe_name:
        query = query.filter(Attempt.probe_name == probe_name)
    results = query.order_by(Attempt.probe_name, Attempt.id).all()
    return [AttemptResponse.model_validate(a) for a in results]


@router.get("/runs/{run_id}/probe-results", response_model=list[ProbeResultResponse])
def get_run_probe_results(run_id: UUID, db: Session = Depends(get_db)) -> list[ProbeResultResponse]:
    """Return all probe results for a specific run, ordered by probe name."""
    run = RunRepository(db).find_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    results = (
        db.query(ProbeResult)
        .filter(ProbeResult.run_id == run_id)
        .order_by(ProbeResult.probe_name, ProbeResult.detector)
        .all()
    )
    return [ProbeResultResponse.model_validate(r) for r in results]


@router.get("/runs/{run_id}", response_model=RunResponse)
def get_run(run_id: UUID, db: Session = Depends(get_db)) -> RunResponse:
    """Get a single run by UUID; 404 if not found."""
    run = RunRepository(db).find_by_id(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return RunResponse.model_validate(run)
