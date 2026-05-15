"""Model for the models table — represents a registered OpenRouter LLM."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import String, Date, Boolean, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy import types
from sqlalchemy.orm import Mapped, mapped_column, relationship

from glokta.database import Base


class UUIDType(types.TypeDecorator):
    """Platform-independent UUID type. Uses String(36) on SQLite, UUID on PostgreSQL."""

    impl = String(36)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return str(value)
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return uuid.UUID(str(value))

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))


class Model(Base):
    """Represents a registered OpenRouter free-tier LLM."""

    __tablename__ = "models"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    runs: Mapped[list["Run"]] = relationship(
        "Run",
        back_populates="model",
        cascade="all, delete-orphan",
    )
    probe_run_queue: Mapped[list["ProbeRunQueue"]] = relationship(
        "ProbeRunQueue",
        back_populates="model",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_models_name", "name", unique=True),
    )