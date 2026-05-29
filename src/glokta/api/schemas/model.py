"""Pydantic schemas for the Model (LLM) resource."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel


class ModelBase(BaseModel):
    name: str
    provider: str
    version: str | None = None
    snapshot_date: date


class ModelCreate(ModelBase):
    pass


class ModelResponse(ModelBase):
    id: UUID
    source: Literal["openrouter", "hf", "manual"] = "manual"
    status: Literal["active", "archived"] = "active"
    created_at: datetime

    model_config = {"from_attributes": True}
