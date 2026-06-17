"""Tests for CTI run queueing (application/cti/eval_service.queue_cti_runs)."""

from datetime import date, datetime, timedelta, timezone

from glokta.application.cti.eval_service import queue_cti_runs
from glokta.domain.cti.tasks import ENABLED_TASKS
from glokta.infrastructure.db.orm import CtiRun, Model


def _model(db, name):
    m = Model(name=name, provider="p", snapshot_date=date(2024, 1, 1), status="active")
    db.add(m)
    db.flush()
    return m


class TestQueueCtiRuns:
    def test_queues_one_run_per_active_model_per_enabled_task(self, db_session):
        _model(db_session, "openrouter/a")
        _model(db_session, "openrouter/b")
        result = queue_cti_runs(db_session, scan_ttl_days=7)
        assert result["queued"] == 2 * len(ENABLED_TASKS)
        assert db_session.query(CtiRun).filter(CtiRun.status == "pending").count() == (
            2 * len(ENABLED_TASKS)
        )

    def test_skips_when_active_run_exists(self, db_session):
        m = _model(db_session, "openrouter/a")
        db_session.add(CtiRun(model_id=m.id, task="rcm", status="running"))
        db_session.flush()
        result = queue_cti_runs(db_session, tasks=["rcm"])
        assert result["queued"] == 0
        assert result["skipped"] == 1

    def test_skips_when_recent_complete_run_exists(self, db_session):
        m = _model(db_session, "openrouter/a")
        db_session.add(
            CtiRun(
                model_id=m.id,
                task="rcm",
                status="complete",
                completed_at=datetime.now(timezone.utc),
            )
        )
        db_session.flush()
        result = queue_cti_runs(db_session, tasks=["rcm"], scan_ttl_days=7)
        assert result["queued"] == 0

    def test_requeues_when_complete_run_is_stale(self, db_session):
        m = _model(db_session, "openrouter/a")
        db_session.add(
            CtiRun(
                model_id=m.id,
                task="rcm",
                status="complete",
                completed_at=datetime.now(timezone.utc) - timedelta(days=30),
            )
        )
        db_session.flush()
        result = queue_cti_runs(db_session, tasks=["rcm"], scan_ttl_days=7)
        assert result["queued"] == 1
