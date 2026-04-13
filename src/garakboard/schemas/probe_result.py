"""Pydantic schemas for ProbeResult."""

from datetime import datetime
from uuid import UUID
from pydantic import BaseModel


class ProbeResultResponse(BaseModel):
    id: int
    run_id: UUID
    probe_name: str
    probe_category: str
    detector: str
    pass_count: int
    fail_count: int
    score: float | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
