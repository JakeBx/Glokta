"""Pydantic schemas for the Model (LLM) resource."""

from datetime import date, datetime
from uuid import UUID
from pydantic import BaseModel


class ModelBase(BaseModel):
    name: str
    provider: str
    version: str | None = None
    snapshot_date: date
    is_active: bool = True


class ModelCreate(ModelBase):
    pass


class ModelResponse(ModelBase):
    id: UUID
    created_at: datetime

    model_config = {"from_attributes": True}
