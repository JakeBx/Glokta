"""Leaderboard API router."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from garakboard.api.deps import get_db
from garakboard.models import Model, Run, ProbeResult
from garakboard.schemas import (
    LeaderboardResponse,
    LeaderboardRow,
    ModelDetailResponse,
    ProbeResultDetail,
)

router = APIRouter()
logger = logging.getLogger(__name__)

def _latest_run_subquery():
    """Return a subquery that selects the most recent complete run id per model.

    Joins runs against the per-model max(created_at) to identify the single
    most recent complete run. Same-millisecond ties are not broken further;
    they are vanishingly rare in practice.
    """
    max_ts = (
        select(
            Run.model_id,
            func.max(Run.created_at).label("max_created_at"),
        )
        .where(Run.status == "complete")
        .group_by(Run.model_id)
        .subquery()
    )

    latest = (
        select(Run.id.label("id"), Run.model_id)
        .join(
            max_ts,
            (Run.model_id == max_ts.c.model_id)
            & (Run.created_at == max_ts.c.max_created_at),
        )
        .where(Run.status == "complete")
        .subquery()
    )
    return latest


def _apply_filters(stmt, probe_category: str | None, model_id: UUID | None):
    """Apply optional probe_category and model_id filters to a select statement."""
    if probe_category:
        stmt = stmt.where(ProbeResult.probe_category == probe_category)
    if model_id:
        stmt = stmt.where(Model.id == model_id)
    return stmt


@router.get("/leaderboard", response_model=LeaderboardResponse)
def get_leaderboard(
    probe_category: str | None = None,
    model_id: UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> LeaderboardResponse:
    """Paginated leaderboard — one row per (model, probe_category) from each model's most recent complete run."""
    latest_run = _latest_run_subquery()

    # Base aggregation: join probe_results to the most-recent-run subquery
    base = (
        select(
            Model.id.label("model_id"),
            Model.name.label("model_name"),
            Model.provider.label("provider"),
            ProbeResult.probe_category.label("probe_category"),
            func.sum(ProbeResult.pass_count).label("total_pass"),
            func.sum(ProbeResult.fail_count).label("total_fail"),
            func.coalesce(func.avg(ProbeResult.score), 0.0).label("score"),
        )
        .join(latest_run, ProbeResult.run_id == latest_run.c.id)
        .join(Model, latest_run.c.model_id == Model.id)
        .group_by(Model.id, Model.name, Model.provider, ProbeResult.probe_category)
        .order_by(func.coalesce(func.avg(ProbeResult.score), 0.0).desc())
    )
    base = _apply_filters(base, probe_category, model_id)

    # Count total rows (before pagination) using the same filtered query as a subquery
    count_stmt = select(func.count()).select_from(base.subquery())
    total = db.execute(count_stmt).scalar() or 0

    # Apply pagination
    offset = (page - 1) * page_size
    results = db.execute(base.offset(offset).limit(page_size)).all()

    rows = [
        LeaderboardRow(
            model_id=row.model_id,
            model_name=row.model_name,
            provider=row.provider,
            probe_category=row.probe_category,
            total_pass=row.total_pass or 0,
            total_fail=row.total_fail or 0,
            score=row.score or 0.0,
        )
        for row in results
    ]

    total_pages = (total + page_size - 1) // page_size if total > 0 else 0

    return LeaderboardResponse(
        rows=rows,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/leaderboard/{model_id}", response_model=ModelDetailResponse)
def get_model_detail(model_id: UUID, db: Session = Depends(get_db)) -> ModelDetailResponse:
    """Per-model detail with all probe results from most recent complete run."""
    model = db.query(Model).filter(Model.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    # Most recent complete run for this model
    run = (
        db.query(Run)
        .filter(Run.model_id == model_id, Run.status == "complete")
        .order_by(Run.created_at.desc())
        .first()
    )

    if not run:
        return ModelDetailResponse(
            model_id=model.id,
            model_name=model.name,
            provider=model.provider,
            probe_results=[],
            summary=None,
        )

    probe_results = (
        db.query(ProbeResult)
        .filter(ProbeResult.run_id == run.id)
        .all()
    )

    probe_result_details = [
        ProbeResultDetail(
            probe_name=pr.probe_name,
            probe_category=pr.probe_category,
            detector=pr.detector,
            pass_count=pr.pass_count,
            fail_count=pr.fail_count,
            score=pr.score,
        )
        for pr in probe_results
    ]

    total_pass = sum(pr.pass_count for pr in probe_results)
    total_fail = sum(pr.fail_count for pr in probe_results)
    avg_score = (
        sum(pr.score or 0.0 for pr in probe_results) / len(probe_results)
        if probe_results else 0.0
    )

    summary = LeaderboardRow(
        model_id=model.id,
        model_name=model.name,
        provider=model.provider,
        probe_category="overall",
        total_pass=total_pass,
        total_fail=total_fail,
        score=avg_score,
    )

    return ModelDetailResponse(
        model_id=model.id,
        model_name=model.name,
        provider=model.provider,
        probe_results=probe_result_details,
        summary=summary,
    )
