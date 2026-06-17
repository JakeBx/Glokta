"""Tests for the resilient bootstrap drain loop (_drain_pending_runs).

A single failing run (e.g. a provider 504) must not abort the whole drain — it should be marked
failed and the loop should continue with the remaining pending runs.
"""

from datetime import date

from glokta.infrastructure.db.orm import CtiRun, Model
from glokta.pipeline.cti_flows import _drain_pending_runs


def _seed_model(db, name):
    model = Model(
        name=name, provider="test", snapshot_date=date(2024, 1, 1),
        cutoff_start=date(2023, 10, 1), cutoff_end=date(2024, 1, 1),
    )
    db.add(model)
    db.flush()
    return model


def test_drain_continues_past_a_failing_run(db_session):
    model = _seed_model(db_session, "openrouter/test/m")
    db_session.add_all([
        CtiRun(model_id=model.id, task="rcm", status="pending"),
        CtiRun(model_id=model.id, task="vsp", status="pending"),
    ])
    db_session.commit()

    seen = []

    def run_fn(run_id, model_name, task):
        seen.append(task)
        if task == "rcm":
            raise RuntimeError("simulated provider 504")
        # vsp succeeds (no scoring needed for this test)

    stats = _drain_pending_runs(db_session, run_fn, max_iterations=20)

    assert set(seen) == {"rcm", "vsp"}                # both runs attempted despite the failure
    assert stats["failed"] >= 1
    assert db_session.query(CtiRun).filter(CtiRun.status == "pending").count() == 0
    assert db_session.query(CtiRun).filter(CtiRun.status == "failed").count() == 1
    assert db_session.query(CtiRun).filter(CtiRun.status == "complete").count() == 1
