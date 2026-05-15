"""Pydantic schemas for ProbeResult and Attempt."""

from datetime import datetime
from typing import Any
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


class AttemptResponse(BaseModel):
    id: int
    run_id: UUID
    probe_name: str
    prompt: str | None = None
    response: str | None = None
    detector_outcome: Any = None
    created_at: datetime

    model_config = {"from_attributes": True}
