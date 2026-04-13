"""Pydantic schemas for leaderboard query responses."""

from uuid import UUID
from pydantic import BaseModel, computed_field


class ProbeResultDetail(BaseModel):
    """Individual probe result for per-model drill-down."""
    probe_name: str
    probe_category: str
    detector: str
    pass_count: int
    fail_count: int
    score: float | None = None

    @computed_field
    @property
    def pass_rate(self) -> float:
        total = self.pass_count + self.fail_count
        return self.pass_count / total if total > 0 else 0.0


class LeaderboardRow(BaseModel):
    """A single row in the leaderboard table — one model's aggregated scores."""
    model_id: UUID
    model_name: str
    provider: str
    probe_category: str
    total_pass: int
    total_fail: int
    score: float  # weighted average score across all probe_results for this model+category

    @computed_field
    @property
    def pass_rate(self) -> float:
        """Pass rate as a fraction 0.0–1.0."""
        total = self.total_pass + self.total_fail
        return self.total_pass / total if total > 0 else 0.0


class LeaderboardResponse(BaseModel):
    """Paginated leaderboard response."""
    rows: list[LeaderboardRow]
    total: int
    page: int
    page_size: int
    total_pages: int


class ModelDetailResponse(BaseModel):
    """Per-model detail with full probe breakdown."""
    model_id: UUID
    model_name: str
    provider: str
    probe_results: list[ProbeResultDetail]
    summary: LeaderboardRow | None = None
