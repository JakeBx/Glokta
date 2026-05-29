"""SQLAlchemy ORM models — all tables consolidated."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy import types
from sqlalchemy.orm import Mapped, mapped_column, relationship

from glokta.infrastructure.db.session import Base


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
    source: Mapped[str] = mapped_column(
        Enum("openrouter", "hf", "manual", name="model_source"),
        nullable=False,
        default="manual",
    )
    status: Mapped[str] = mapped_column(
        Enum("active", "archived", name="model_status"),
        nullable=False,
        default="active",
    )
    last_scan_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

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

    __table_args__ = (Index("ix_models_name", "name", unique=True),)


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
        default="scheduled",
    )
    # Per-run overrides (NULL = use global settings)
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

    # Run metadata — populated by the worker
    garak_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    garak_config: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_output: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    __table_args__ = (Index("ix_runs_model_id_status", "model_id", "status"),)


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

    run: Mapped["Run"] = relationship("Run", back_populates="probe_results")

    __table_args__ = (
        Index("ix_probe_results_probe_category", "probe_category"),
        Index("ix_probe_results_run_id", "run_id"),
    )


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

    run: Mapped["Run"] = relationship("Run", back_populates="attempts")


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

    model: Mapped["Model"] = relationship("Model", back_populates="probe_run_queue")

    __table_args__ = (
        Index(
            "ix_probe_run_queue_model_probe_category_status",
            "model_id",
            "probe_category",
            "status",
        ),
    )


class ScanDlq(Base):
    """Dead-letter queue for failed scans and models with incomplete probe coverage."""

    __tablename__ = "scan_dlq"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(),
        ForeignKey("models.id"),
        nullable=False,
    )
    run_id: Mapped[uuid.UUID | None] = mapped_column(
        UUIDType(),
        ForeignKey("runs.id"),
        nullable=True,
    )
    reason: Mapped[str] = mapped_column(
        Enum("scan_failed", "incomplete_coverage", name="dlq_reason"),
        nullable=False,
    )
    missing_categories: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index("ix_scan_dlq_model_id", "model_id"),
        Index("ix_scan_dlq_run_id", "run_id"),
    )
