"""Model for the runs table — represents a single garak scan execution."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Enum, Integer, String, Text, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from glokta.database import Base
from glokta.models.model import UUIDType


class Run(Base):
    """Represents a single garak scan execution."""

    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(),
        primary_key=True,
        default=uuid.uuid4,
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(),
        ForeignKey("models.id"),
        nullable=False,
    )
    triggered_by: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="api",
    )
    # Per-run overrides for training data collection runs (NULL = use global settings)
    probe_categories_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    probe_prompt_cap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    parallel_attempts_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scan_timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(
        Enum("pending", "running", "complete", "failed", name="run_status"),
        nullable=False,
        default="pending",
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Run metadata — populated for all run types by the worker
    garak_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    garak_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Community run reproducibility metadata
    scanned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    submitted_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    config_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    jsonl_manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verification_requested_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Self-referential FK: on a verified Run, points to the community Run that prompted it
    source_community_run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(),
        ForeignKey("runs.id"),
        nullable=True,
    )

    # Relationships
    model: Mapped["Model"] = relationship("Model", back_populates="runs")
    probe_results: Mapped[list["ProbeResult"]] = relationship(
        "ProbeResult",
        back_populates="run",
        cascade="all, delete-orphan",
    )
    attempts: Mapped[list["Attempt"]] = relationship(
        "Attempt",
        back_populates="run",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_runs_model_id_status", "model_id", "status"),
    )