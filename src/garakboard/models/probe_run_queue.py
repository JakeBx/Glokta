"""Model for the probe_run_queue table — tracks probe coverage across batched rate-limited runs."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Enum, Integer, String, Date, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from garakboard.database import Base
from garakboard.models.model import UUIDType


class ProbeRunQueue(Base):
    """Tracks probe coverage across batched rate-limited runs."""

    __tablename__ = "probe_run_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(),
        ForeignKey("models.id"),
        nullable=False,
    )
    probe_category: Mapped[str] = mapped_column(String(255), nullable=False)
    scheduled_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        Enum("pending", "running", "complete", "failed", name="probe_queue_status"),
        nullable=False,
        default="pending",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    model: Mapped["Model"] = relationship("Model", back_populates="probe_run_queue")

    __table_args__ = (
        Index("ix_probe_run_queue_model_probe_category_status", "model_id", "probe_category", "status"),
    )