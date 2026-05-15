"""Model for the attempts table — raw attempt detail for drill-down."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Integer, String, Text, DateTime, JSON, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from glokta.database import Base
from glokta.models.model import UUIDType


class Attempt(Base):
    """Raw attempt detail for drill-down. detector_outcome stored as JSON."""

    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(),
        ForeignKey("runs.id"),
        nullable=False,
    )
    probe_name: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    detector_outcome: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    run: Mapped["Run"] = relationship("Run", back_populates="attempts")