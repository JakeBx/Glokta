"""Model for the probe_results table — derived leaderboard data from garak eval entries."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Integer, String, Float, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from garakboard.database import Base
from garakboard.models.model import UUIDType


class ProbeResult(Base):
    """Derived leaderboard data from garak eval entries."""

    __tablename__ = "probe_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(),
        ForeignKey("runs.id"),
        nullable=False,
    )
    probe_name: Mapped[str] = mapped_column(String(255), nullable=False)
    probe_category: Mapped[str] = mapped_column(String(255), nullable=False)
    detector: Mapped[str] = mapped_column(String(255), nullable=False)
    pass_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fail_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    run: Mapped["Run"] = relationship("Run", back_populates="probe_results")

    __table_args__ = (
        Index("ix_probe_results_probe_category", "probe_category"),
        Index("ix_probe_results_run_id", "run_id"),
    )