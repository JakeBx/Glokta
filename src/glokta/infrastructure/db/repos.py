"""Repository classes — encapsulate all SQLAlchemy query logic."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from glokta.infrastructure.db.orm import (
    CtiItem,
    CtiResult,
    CtiRun,
    Model,
    ProbeResult,
    Run,
    ScanDlq,
)


class ModelRepository:
    def __init__(self, session: Session) -> None:
        self._db = session

    def find_by_id(self, model_id: uuid.UUID) -> Model | None:
        return self._db.query(Model).filter(Model.id == model_id).first()

    def find_by_name(self, name: str) -> Model | None:
        return self._db.query(Model).filter(Model.name == name).first()

    def find_or_create(self, name: str) -> Model:
        """Return existing Model by name, creating it if absent."""
        model = self.find_by_name(name)
        if model is None:
            provider = name.split("/")[1] if "/" in name else name
            model = Model(
                name=name,
                provider=provider,
                snapshot_date=date.today(),
            )
            self._db.add(model)
            self._db.flush()
        return model

    def upsert_from_source(self, name: str, provider: str, source: str) -> Model:
        """Find model by name or create it with the given source; always set status=active."""
        model = self.find_by_name(name)
        if model is None:
            model = Model(
                name=name,
                provider=provider,
                source=source,
                snapshot_date=date.today(),
            )
            self._db.add(model)
        else:
            model.status = "active"
        self._db.flush()
        return model

    def list_active(self) -> list[Model]:
        return self._db.query(Model).filter(Model.status == "active").order_by(Model.name).all()


class RunRepository:
    def __init__(self, session: Session) -> None:
        self._db = session

    def find_by_id(self, run_id: uuid.UUID) -> Run | None:
        return self._db.query(Run).filter(Run.id == run_id).first()

    def list_all(self, status: str | None = None) -> list[Run]:
        query = self._db.query(Run).order_by(Run.created_at.desc())
        if status is not None:
            query = query.filter(Run.status == status)
        return query.all()

    def pending_one_locked(self) -> Run | None:
        """Fetch one pending run using SKIP LOCKED; falls back to plain query (SQLite)."""
        try:
            return (
                self._db.query(Run)
                .filter(Run.status == "pending")
                .with_for_update(skip_locked=True)
                .first()
            )
        except Exception:
            self._db.rollback()
            return self._db.query(Run).filter(Run.status == "pending").first()

    def stale_running(self, cutoff: datetime) -> list[Run]:
        """Return runs stuck in 'running' state since before cutoff."""
        return (
            self._db.query(Run)
            .filter(Run.status == "running", Run.started_at <= cutoff)
            .all()
        )


class ProbeResultRepository:
    def __init__(self, session: Session) -> None:
        self._db = session

    def done_probe_names_for(self, run_id: str) -> set[str]:
        """Return probe names already completed in a prior scan attempt (for resume support)."""
        return {
            row[0]
            for row in self._db.query(ProbeResult.probe_name)
            .filter(ProbeResult.run_id == run_id)
            .distinct()
            .all()
        }

    def covered_categories_for(self, run_id: uuid.UUID) -> set[str]:
        """Return probe_category values present in a run's results (for coverage checks)."""
        return {
            row[0]
            for row in self._db.query(ProbeResult.probe_category)
            .filter(ProbeResult.run_id == run_id)
            .distinct()
            .all()
        }


class ScanDlqRepository:
    def __init__(self, session: Session) -> None:
        self._db = session

    def create(
        self,
        model_id: uuid.UUID,
        reason: str,
        run_id: uuid.UUID | None = None,
        missing_categories: str | None = None,
        error_message: str | None = None,
    ) -> ScanDlq:
        entry = ScanDlq(
            model_id=model_id,
            run_id=run_id,
            reason=reason,
            missing_categories=missing_categories,
            error_message=error_message,
        )
        self._db.add(entry)
        self._db.flush()
        return entry

    def recent_for_model(self, model_id: uuid.UUID, limit: int = 10) -> list[ScanDlq]:
        return (
            self._db.query(ScanDlq)
            .filter(ScanDlq.model_id == model_id)
            .order_by(ScanDlq.created_at.desc())
            .limit(limit)
            .all()
        )


class CtiItemRepository:
    def __init__(self, session: Session) -> None:
        self._db = session

    def active_for(self, task: str, external_id: str) -> CtiItem | None:
        """Return the current active item for a (task, external_id), if any."""
        return (
            self._db.query(CtiItem)
            .filter(
                CtiItem.task == task,
                CtiItem.external_id == external_id,
                CtiItem.status == "active",
            )
            .first()
        )

    def add(self, item: CtiItem) -> CtiItem:
        self._db.add(item)
        self._db.flush()
        return item

    def slice_for_task(
        self,
        task: str,
        exclude_withheld: bool = True,
        before: date | None = None,
        after: date | None = None,
        limit: int | None = None,
        exclude_ids: set[uuid.UUID] | None = None,
    ) -> list[CtiItem]:
        """Active items for a task, ordered by first_available_date.

        ``before``/``after`` bound first_available_date (inclusive); ``exclude_withheld``
        drops the rolling private holdout slice; ``exclude_ids`` drops already-scored items
        BEFORE ``limit`` is applied, so a capped run resumes onto the next page rather than
        re-seeing an already-scored first page.
        """
        query = self._db.query(CtiItem).filter(
            CtiItem.task == task,
            CtiItem.status == "active",
        )
        if exclude_withheld:
            query = query.filter(CtiItem.withhold.is_(False))
        if exclude_ids:
            query = query.filter(CtiItem.id.notin_(exclude_ids))
        if before is not None:
            query = query.filter(CtiItem.first_available_date <= before)
        if after is not None:
            query = query.filter(CtiItem.first_available_date >= after)
        query = query.order_by(CtiItem.first_available_date)
        if limit is not None:
            query = query.limit(limit)
        return query.all()


class CtiRunRepository:
    def __init__(self, session: Session) -> None:
        self._db = session

    def find_by_id(self, run_id: uuid.UUID) -> CtiRun | None:
        return self._db.query(CtiRun).filter(CtiRun.id == run_id).first()

    def pending_one_locked(self) -> CtiRun | None:
        """Fetch one pending CTI run using SKIP LOCKED; falls back to plain query (SQLite)."""
        try:
            return (
                self._db.query(CtiRun)
                .filter(CtiRun.status == "pending")
                .with_for_update(skip_locked=True)
                .first()
            )
        except Exception:
            self._db.rollback()
            return self._db.query(CtiRun).filter(CtiRun.status == "pending").first()

    def has_active_run(self, model_id: uuid.UUID, task: str) -> bool:
        """True if a pending/running run already exists for this (model, task)."""
        return (
            self._db.query(CtiRun)
            .filter(
                CtiRun.model_id == model_id,
                CtiRun.task == task,
                CtiRun.status.in_(["pending", "running"]),
            )
            .first()
            is not None
        )

    def stale_running(self, cutoff: datetime) -> list[CtiRun]:
        return (
            self._db.query(CtiRun)
            .filter(CtiRun.status == "running", CtiRun.started_at <= cutoff)
            .all()
        )

    def latest_complete_per_task_all_models(self) -> list[CtiRun]:
        """Most recent complete run per (model_id, task) across all models."""
        rows = (
            self._db.query(CtiRun)
            .filter(CtiRun.status == "complete")
            .order_by(CtiRun.model_id, CtiRun.task, CtiRun.completed_at.desc())
            .all()
        )
        seen: set[tuple] = set()
        result: list[CtiRun] = []
        for row in rows:
            key = (row.model_id, row.task)
            if key not in seen:
                seen.add(key)
                result.append(row)
        return result

    def complete_for_model(self, model_id: uuid.UUID) -> list[CtiRun]:
        """All complete runs for one model across all tasks, newest first."""
        return (
            self._db.query(CtiRun)
            .filter(CtiRun.model_id == model_id, CtiRun.status == "complete")
            .order_by(CtiRun.task, CtiRun.completed_at.desc())
            .all()
        )


class CtiResultRepository:
    def __init__(self, session: Session) -> None:
        self._db = session

    def scored_item_ids_for(self, run_id: uuid.UUID) -> set[uuid.UUID]:
        """Item ids already scored in a run (resume/dedup support)."""
        return {
            row[0]
            for row in self._db.query(CtiResult.item_id)
            .filter(CtiResult.run_id == run_id)
            .distinct()
            .all()
        }

    def for_run(self, run_id: uuid.UUID) -> list[CtiResult]:
        return self._db.query(CtiResult).filter(CtiResult.run_id == run_id).all()
