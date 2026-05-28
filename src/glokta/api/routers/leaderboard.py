"""Leaderboard API router."""

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from glokta.api.deps import get_db
from glokta.models import Model, Run, ProbeResult
from glokta.risks import ACTIVE_RISKS, compute_risk_pass_rates
from glokta.schemas import (
    LeaderboardResponse,
    LeaderboardRow,
    ModelDetailResponse,
    ProbeResultDetail,
    RiskLeaderboardResponse,
    RiskModelRow,
    TrendPoint,
    TrendResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)

def _latest_run_subquery():
    max_ts = (
        select(
            Run.model_id,
            func.max(Run.created_at).label("max_created_at"),
        )
        .where(Run.status == "complete")
        .group_by(Run.model_id)
        .subquery()
    )
    return (
        select(Run.id.label("id"), Run.model_id)
        .join(
            max_ts,
            (Run.model_id == max_ts.c.model_id)
            & (Run.created_at == max_ts.c.max_created_at),
        )
        .where(Run.status == "complete")
        .subquery()
    )


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

    # Correlated subquery: triggered_by of the most recent complete run per model
    latest_origin = (
        select(Run.triggered_by)
        .where(Run.model_id == Model.id, Run.status == "complete")
        .order_by(Run.completed_at.desc())
        .limit(1)
        .correlate(Model)
        .scalar_subquery()
    )

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
            latest_origin.label("origin"),
        )
        .join(latest_run, ProbeResult.run_id == latest_run.c.id)
        .join(Model, latest_run.c.model_id == Model.id)
        .group_by(Model.id, Model.name, Model.provider, ProbeResult.probe_category)
        .order_by(func.coalesce(func.avg(ProbeResult.score), 0.0).asc())
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
            origin=row.origin or "api",
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

    run = (
        db.query(Run)
        .filter(Run.model_id == model_id, Run.status == "complete")
        .order_by(Run.created_at.desc())
        .first()
    )

    probe_results = (
        db.query(ProbeResult).filter(ProbeResult.run_id == run.id).all()
        if run
        else []
    )

    if not probe_results:
        return ModelDetailResponse(
            model_id=model.id,
            model_name=model.name,
            provider=model.provider,
            probe_results=[],
            summary=None,
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
        run_id=run.id if run else None,
        probe_results=probe_result_details,
        summary=summary,
    )


@router.get("/risk-leaderboard", response_model=RiskLeaderboardResponse)
def get_risk_leaderboard(
    included_risks: str | None = Query(default=None, description="Comma-separated risk categories to include"),
    db: Session = Depends(get_db),
) -> RiskLeaderboardResponse:
    """Risk-based leaderboard — one row per model, scored by mean pass rate across risk categories.

    Pass rate per risk = sum(pass) / sum(pass + fail) for all probes in that category.
    Overall = arithmetic mean of per-risk pass rates for the included risks.
    Sorted by overall_pass_rate descending (safest first).
    """
    risks = included_risks.split(",") if included_risks else ACTIVE_RISKS
    risks = [r.strip() for r in risks if r.strip()]

    latest_run = _latest_run_subquery()

    # Fetch all probe results for latest runs, filtered to included risk categories
    rows = (
        db.execute(
            select(
                Model.id.label("model_id"),
                Model.name.label("model_name"),
                Model.provider.label("provider"),
                ProbeResult.probe_category.label("probe_category"),
                func.sum(ProbeResult.pass_count).label("pass_count"),
                func.sum(ProbeResult.fail_count).label("fail_count"),
            )
            .join(latest_run, ProbeResult.run_id == latest_run.c.id)
            .join(Model, latest_run.c.model_id == Model.id)
            .where(ProbeResult.probe_category.in_(risks))
            .group_by(Model.id, Model.name, Model.provider, ProbeResult.probe_category)
        )
        .all()
    )

    # Group by model
    models_data: dict[UUID, dict] = {}
    for row in rows:
        mid = row.model_id
        if mid not in models_data:
            models_data[mid] = {
                "model_id": mid,
                "model_name": row.model_name,
                "provider": row.provider,
                "probe_results": [],
            }
        models_data[mid]["probe_results"].append({
            "probe_category": row.probe_category,
            "pass_count": row.pass_count or 0,
            "fail_count": row.fail_count or 0,
        })

    model_rows: list[RiskModelRow] = []
    for data in models_data.values():
        per_risk, overall = compute_risk_pass_rates(data["probe_results"], included_risks=risks)
        model_rows.append(RiskModelRow(
            model_id=data["model_id"],
            model_name=data["model_name"],
            provider=data["provider"],
            overall_pass_rate=overall,
            per_risk=per_risk,
        ))

    model_rows.sort(key=lambda r: r.overall_pass_rate if r.overall_pass_rate is not None else -1, reverse=True)

    return RiskLeaderboardResponse(models=model_rows, included_risks=risks)


@router.get("/trends/{model_id}", response_model=TrendResponse)
def get_trends(
    model_id: UUID,
    included_risks: str | None = Query(default=None, description="Comma-separated risk categories to include"),
    db: Session = Depends(get_db),
) -> TrendResponse:
    """All historical scan results for a model, ordered by completion date.

    Returns one TrendPoint per completed run, each with per-risk pass rates and
    an overall mean pass rate across the included risks.
    """
    model = db.query(Model).filter(Model.id == model_id).first()
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    risks = included_risks.split(",") if included_risks else ACTIVE_RISKS
    risks = [r.strip() for r in risks if r.strip()]

    runs = (
        db.query(Run)
        .filter(Run.model_id == model_id, Run.status == "complete")
        .order_by(Run.completed_at.asc())
        .all()
    )

    points: list[TrendPoint] = []
    for run in runs:
        probe_results = db.query(ProbeResult).filter(ProbeResult.run_id == run.id).all()
        pr_dicts = [
            {"probe_category": pr.probe_category, "pass_count": pr.pass_count, "fail_count": pr.fail_count}
            for pr in probe_results
        ]
        per_risk, overall = compute_risk_pass_rates(pr_dicts, included_risks=risks)
        points.append(TrendPoint(
            run_id=run.id,
            completed_at=run.completed_at,
            per_risk=per_risk,
            overall_pass_rate=overall,
        ))

    return TrendResponse(model_id=model.id, model_name=model.name, points=points)
