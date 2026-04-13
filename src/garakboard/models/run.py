"""Model for the runs table — represents a single garak scan execution."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Enum, String, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from garakboard.database import Base
from garakboard.models.model import UUIDType


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