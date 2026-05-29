"""Repository classes — encapsulate all SQLAlchemy query logic."""

import uuid
from datetime import date, datetime, timezone

from sqlalchemy.orm import Session

from glokta.infrastructure.db.orm import Model, Run, ProbeResult


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

    def list_active(self) -> list[Model]:
        return self._db.query(Model).filter(Model.is_active.is_(True)).order_by(Model.name).all()


class RunRepository:
    def __init__(self, session: Session) -> None:
        self._db = session

    def find_by_id(self, run_id: uuid.UUID) -> Run | None:
        return self._db.query(Run).filter(Run.id == run_id).first()

    def find_verified_for(self, community_run_id: uuid.UUID) -> Run | None:
        return (
            self._db.query(Run)
            .filter(
                Run.source_community_run_id == community_run_id,
                Run.triggered_by == "verified",
            )
            .first()
        )

    def list_all(
        self,
        status: str | None = None,
        verification_requested: bool | None = None,
    ) -> list[Run]:
        query = self._db.query(Run).order_by(Run.created_at.desc())
        if status is not None:
            query = query.filter(Run.status == status)
        if verification_requested is True:
            query = query.filter(Run.verification_requested_at.isnot(None))
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
