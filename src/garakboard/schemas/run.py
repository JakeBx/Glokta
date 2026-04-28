"""Pydantic schemas for the Run resource."""

from datetime import datetime
from uuid import UUID
from typing import Literal
from pydantic import BaseModel

RunStatus = Literal["pending", "running", "complete", "failed"]
RunOrigin = Literal["api", "scheduled", "community", "verified"]


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
    # Reproducibility / community metadata
    garak_version: str | None = None
    scanned_at: datetime | None = None
    submitted_by: str | None = None
    garak_config: str | None = None
    config_hash: str | None = None
    jsonl_manifest_hash: str | None = None
    verification_requested_at: datetime | None = None
    source_community_run_id: UUID | None = None
    # Populated by GET /api/runs/{id} for community runs
    verified_run_id: UUID | None = None

    model_config = {"from_attributes": True}


class RunSummaryRow(BaseModel):
    model_name: str
    provider: str
    pending: int
    running: int
    complete: int
    failed: int
    latest_origin: str = "api"
