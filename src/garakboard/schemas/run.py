"""Pydantic schemas for the Run resource."""

from datetime import datetime
from uuid import UUID
from typing import Literal
from pydantic import BaseModel

RunStatus = Literal["pending", "running", "complete", "failed"]


class RunCreate(BaseModel):
    model_id: UUID
    probe_categories: list[str] = []


class RunResponse(BaseModel):
    id: UUID
    model_id: UUID
    triggered_by: str
    status: RunStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
