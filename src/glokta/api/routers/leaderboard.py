"""Leaderboard API router — thin adapter over LeaderboardService."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from glokta.api.deps import get_db
from glokta.application.leaderboard import LeaderboardService
from glokta.api.schemas import (
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


@router.get("/leaderboard", response_model=LeaderboardResponse)
def get_leaderboard(
    probe_category: str | None = None,
    model_id: UUID | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> LeaderboardResponse:
    """Paginated leaderboard — one row per (model, probe_category) from each model's most recent complete run."""
    svc = LeaderboardService(db)
    data = svc.get_leaderboard(probe_category=probe_category, model_id=model_id, page=page, page_size=page_size)
    return LeaderboardResponse(
        rows=[LeaderboardRow(**r) for r in data["rows"]],
        total=data["total"],
        page=data["page"],
        page_size=data["page_size"],
        total_pages=data["total_pages"],
    )


@router.get("/leaderboard/{model_id}", response_model=ModelDetailResponse)
def get_model_detail(model_id: UUID, db: Session = Depends(get_db)) -> ModelDetailResponse:
    """Per-model detail with all probe results from most recent complete run."""
    svc = LeaderboardService(db)
    data = svc.get_model_detail(model_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return ModelDetailResponse(
        model_id=data["model_id"],
        model_name=data["model_name"],
        provider=data["provider"],
        run_id=data.get("run_id"),
        probe_results=[ProbeResultDetail(**pr) for pr in data["probe_results"]],
        summary=LeaderboardRow(**data["summary"]) if data["summary"] else None,
    )


@router.get("/risk-leaderboard", response_model=RiskLeaderboardResponse)
def get_risk_leaderboard(
    included_risks: str | None = Query(default=None, description="Comma-separated risk categories to include"),
    db: Session = Depends(get_db),
) -> RiskLeaderboardResponse:
    """Risk-based leaderboard — one row per model, scored by mean pass rate across risk categories."""
    svc = LeaderboardService(db)
    risks = [r.strip() for r in included_risks.split(",")] if included_risks else None
    data = svc.get_risk_leaderboard(included_risks=risks)
    return RiskLeaderboardResponse(
        models=[RiskModelRow(**m) for m in data["models"]],
        included_risks=data["included_risks"],
    )


@router.get("/trends/{model_id}", response_model=TrendResponse)
def get_trends(
    model_id: UUID,
    included_risks: str | None = Query(default=None, description="Comma-separated risk categories to include"),
    db: Session = Depends(get_db),
) -> TrendResponse:
    """All historical scan results for a model, ordered by completion date."""
    svc = LeaderboardService(db)
    risks = [r.strip() for r in included_risks.split(",")] if included_risks else None
    data = svc.get_trends(model_id, included_risks=risks)
    if data is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return TrendResponse(
        model_id=data["model_id"],
        model_name=data["model_name"],
        points=[TrendPoint(**p) for p in data["points"]],
    )
