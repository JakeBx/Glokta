"""Pydantic schemas for CTI benchmark API endpoints."""

from datetime import datetime

from pydantic import BaseModel


class CtiTaskScore(BaseModel):
    task: str
    prequential_score: float | None
    item_count: int
    scored_count: int
    run_id: str
    completed_at: datetime | None
    # forecast task only — pulled from run.config["auc"]
    auc: float | None


class CtiLeaderboardRow(BaseModel):
    model_id: str
    model_name: str
    provider: str
    tasks: dict[str, CtiTaskScore]
    overall: float | None


class CtiLeaderboardResponse(BaseModel):
    models: list[CtiLeaderboardRow]


class CtiTaskRunRow(BaseModel):
    """One row in the per-model task summary table."""
    task: str
    run_id: str
    prequential_score: float | None
    item_count: int
    scored_count: int
    completed_at: datetime | None
    auc: float | None


class CtiModelDetailResponse(BaseModel):
    model_id: str
    model_name: str
    provider: str
    runs: list[CtiTaskRunRow]


class CtiResultRow(BaseModel):
    item_id: str
    score: float | None
    correct: bool | None
    pre_cutoff: bool | None
    score_summary: str
