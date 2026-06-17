"""CTI benchmark API router."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from glokta.api.deps import get_db
from glokta.api.schemas.cti import (
    CtiLeaderboardResponse,
    CtiLeaderboardRow,
    CtiModelDetailResponse,
    CtiResultRow,
    CtiTaskRunRow,
    CtiTaskScore,
)
from glokta.infrastructure.db.orm import CtiRun, Model
from glokta.infrastructure.db.repos import CtiResultRepository, CtiRunRepository, ModelRepository

router = APIRouter()

_TASK_ORDER = ["rcm", "vsp", "ate", "taa", "forecast", "syn"]


def _score_summary(task: str, breakdown: dict | None) -> str:
    """Compact human-readable string from a score_breakdown dict."""
    if not breakdown:
        return ""
    try:
        if task in ("rcm", "ate"):
            return f"F1 p={breakdown['precision']:.2f} r={breakdown['recall']:.2f}"
        if task == "vsp":
            sev = "✓" if breakdown.get("severity_match") else "✗"
            return f"MAD={breakdown['mad']:.2f} sev={sev}"
        if task == "taa":
            return f"result={breakdown.get('result', '?')} pred={breakdown.get('pred_canonical', '?')}"
        if task == "forecast":
            return f"prob={breakdown.get('prob', '?'):.2f} brier={breakdown.get('brier', '?'):.3f}"
        if task == "syn":
            rec = breakdown.get("recall")
            faith = breakdown.get("faithfulness")
            cal = breakdown.get("calibration")
            parts = []
            if rec is not None:
                parts.append(f"recall={rec:.2f}")
            if faith is not None:
                parts.append(f"faith={faith:.2f}")
            if cal is not None:
                parts.append(f"cal={cal:.2f}")
            return " ".join(parts)
    except Exception:
        pass
    return str(breakdown)


def _task_score_from_run(run: CtiRun) -> CtiTaskScore:
    auc: float | None = None
    if run.task == "forecast" and run.config:
        auc = run.config.get("auc")
    return CtiTaskScore(
        task=run.task,
        prequential_score=run.prequential_score,
        item_count=run.item_count,
        scored_count=run.scored_count,
        run_id=str(run.id),
        completed_at=run.completed_at,
        auc=auc,
    )


@router.get("/cti/leaderboard", response_model=CtiLeaderboardResponse)
def get_cti_leaderboard(db: Session = Depends(get_db)) -> CtiLeaderboardResponse:
    """Aggregated CTI leaderboard: one row per model, one column per task."""
    runs = CtiRunRepository(db).latest_complete_per_task_all_models()

    # Group by model_id
    by_model: dict[uuid.UUID, list[CtiRun]] = {}
    for run in runs:
        by_model.setdefault(run.model_id, []).append(run)

    model_rows: list[CtiLeaderboardRow] = []
    for model_id, model_runs in by_model.items():
        # Resolve model name/provider via the first run's relationship (already loaded)
        # Fall back to ModelRepository if needed
        first_run = model_runs[0]
        if first_run.model is not None:
            model_name = first_run.model.name
            provider = first_run.model.provider
        else:
            m = ModelRepository(db).find_by_id(model_id)
            model_name = m.name if m else str(model_id)
            provider = m.provider if m else ""

        tasks: dict[str, CtiTaskScore] = {
            run.task: _task_score_from_run(run) for run in model_runs
        }

        scores = [ts.prequential_score for ts in tasks.values() if ts.prequential_score is not None]
        overall = sum(scores) / len(scores) if scores else None

        model_rows.append(CtiLeaderboardRow(
            model_id=str(model_id),
            model_name=model_name,
            provider=provider,
            tasks=tasks,
            overall=overall,
        ))

    # Sort by overall desc (None last)
    model_rows.sort(key=lambda r: r.overall if r.overall is not None else -1.0, reverse=True)
    return CtiLeaderboardResponse(models=model_rows)


@router.get("/cti/model/{model_id}", response_model=CtiModelDetailResponse)
def get_cti_model_detail(model_id: uuid.UUID, db: Session = Depends(get_db)) -> CtiModelDetailResponse:
    """Latest complete run per task for one model."""
    m = ModelRepository(db).find_by_id(model_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Model not found")

    runs = CtiRunRepository(db).complete_for_model(model_id)
    # Dedup to latest per task
    seen: set[str] = set()
    task_runs: list[CtiTaskRunRow] = []
    for run in runs:
        if run.task not in seen:
            seen.add(run.task)
            auc: float | None = run.config.get("auc") if run.task == "forecast" and run.config else None
            task_runs.append(CtiTaskRunRow(
                task=run.task,
                run_id=str(run.id),
                prequential_score=run.prequential_score,
                item_count=run.item_count,
                scored_count=run.scored_count,
                completed_at=run.completed_at,
                auc=auc,
            ))

    task_runs.sort(key=lambda r: _TASK_ORDER.index(r.task) if r.task in _TASK_ORDER else 99)

    return CtiModelDetailResponse(
        model_id=str(model_id),
        model_name=m.name,
        provider=m.provider,
        runs=task_runs,
    )


@router.get("/cti/runs/{run_id}/results", response_model=list[CtiResultRow])
def get_cti_run_results(run_id: uuid.UUID, db: Session = Depends(get_db)) -> list[CtiResultRow]:
    """Per-item results for a single CTI run."""
    run = CtiRunRepository(db).find_by_id(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")

    results = CtiResultRepository(db).for_run(run_id)
    return [
        CtiResultRow(
            item_id=str(r.item_id),
            score=r.score,
            correct=r.correct,
            pre_cutoff=r.pre_cutoff,
            score_summary=_score_summary(run.task, r.score_breakdown),
        )
        for r in results
    ]
