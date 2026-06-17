"""SQLAlchemy ORM models — all tables consolidated."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
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
    # Per-model training cutoff (fuzzy/undisclosed → stored as a range, not a point).
    # Drives pre/post-cutoff slicing of CTI items; null → treated as post-cutoff.
    cutoff_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    cutoff_end: Mapped[date | None] = mapped_column(Date, nullable=True)
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


# ---------------------------------------------------------------------------
# CTI benchmark tables — parallel to the garak Run/ProbeResult path. These hold
# dated, provenance-pinned benchmark items, per-(model, task) evaluation runs,
# per-item scored results, and the reference tables that back ATE/TAA/Forecast.
# ---------------------------------------------------------------------------

# Shared across cti_item and cti_run, so define the named enum once.
_cti_task_enum = Enum(
    "rcm", "vsp", "ate", "taa", "forecast", "syn", name="cti_task"
)


class CtiItem(Base):
    """One (task, input, label) benchmark tuple. A single CVE yields two items
    (one RCM, one VSP) sharing external_id, so every item is uniformly scorable."""

    __tablename__ = "cti_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), primary_key=True, default=uuid.uuid4
    )
    task: Mapped[str] = mapped_column(_cti_task_enum, nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)

    input_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_ref: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    label: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    label_provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source_revision: Mapped[str | None] = mapped_column(String(255), nullable=True)

    input_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    label_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Temporal anchor = max(input_date, label_date); the slicing key.
    first_available_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    authority_agreement: Mapped[str] = mapped_column(
        Enum("single", "agree", "disagree", name="cti_authority_agreement"),
        nullable=False,
        default="single",
    )
    difficulty: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    withhold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(
        Enum("active", "superseded", name="cti_item_status"),
        nullable=False,
        default="active",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (
        Index("ix_cti_items_task_first_available", "task", "first_available_date"),
        Index("ix_cti_items_external_id", "external_id"),
        Index("ix_cti_items_task_withhold_status", "task", "withhold", "status"),
    )


class CtiRun(Base):
    """One evaluation of one model against one CTI task (analog of Run)."""

    __tablename__ = "cti_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), primary_key=True, default=uuid.uuid4
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("models.id"), nullable=False
    )
    task: Mapped[str] = mapped_column(_cti_task_enum, nullable=False)
    triggered_by: Mapped[str] = mapped_column(
        String(255), nullable=False, default="scheduled"
    )
    status: Mapped[str] = mapped_column(
        Enum("pending", "running", "complete", "failed", name="cti_run_status"),
        nullable=False,
        default="pending",
    )
    # Snapshot of the model's cutoff range at run time (range, not a point).
    model_cutoff_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    model_cutoff_end: Mapped[date | None] = mapped_column(Date, nullable=True)

    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scored_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prequential_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    config: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    model: Mapped["Model"] = relationship("Model")
    results: Mapped[list["CtiResult"]] = relationship(
        "CtiResult", back_populates="run", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_cti_runs_model_task_status", "model_id", "task", "status"),)


class CtiResult(Base):
    """One model output + score for one item in a run (ProbeResult+Attempt merged)."""

    __tablename__ = "cti_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("cti_runs.id"), nullable=False
    )
    item_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("cti_items.id"), nullable=False
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        UUIDType(), ForeignKey("models.id"), nullable=False
    )

    prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    prompt_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_output: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    score_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    correct: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pre_cutoff: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    run: Mapped["CtiRun"] = relationship("CtiRun", back_populates="results")

    __table_args__ = (
        Index("ix_cti_results_run_id", "run_id"),
        Index("ix_cti_results_item_id", "item_id"),
        Index("ix_cti_results_model_id", "model_id"),
    )


class CtiAttackTechnique(Base):
    """MITRE ATT&CK technique reference (no items) — validates/normalises ATE labels."""

    __tablename__ = "cti_attack_techniques"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    technique_id: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    tactic: Mapped[str | None] = mapped_column(String(255), nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (Index("ix_cti_attack_techniques_technique_id", "technique_id"),)


class CtiThreatActor(Base):
    """Threat-actor reference (no items) — alias + related-group graph for TAA scoring."""

    __tablename__ = "cti_threat_actors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    aliases: Mapped[list | None] = mapped_column(JSON, nullable=True)
    techniques: Mapped[list | None] = mapped_column(JSON, nullable=True)
    related_groups: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (Index("ix_cti_threat_actors_canonical_name", "canonical_name"),)


class CtiKev(Base):
    """CISA KEV reference (no items) — resolver for the exploitation Forecast task."""

    __tablename__ = "cti_kev"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cve_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    date_added: Mapped[date] = mapped_column(Date, nullable=False)
    vendor: Mapped[str | None] = mapped_column(String(255), nullable=True)
    product: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    __table_args__ = (Index("ix_cti_kev_cve_id", "cve_id"),)
