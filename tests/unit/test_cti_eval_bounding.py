"""Tests for eval safety rails: slice limit, per-run cap, incremental commit."""

from datetime import date

from glokta.application.cti.eval_service import execute_cti_run
from glokta.infrastructure.db.orm import CtiItem, CtiResult, CtiRun, Model
from glokta.infrastructure.db.repos import CtiItemRepository


def _model(db):
    m = Model(name="openrouter/m", provider="p", snapshot_date=date(2024, 1, 1),
              cutoff_end=date(2024, 1, 1))
    db.add(m)
    db.flush()
    return m


def _items(db, n):
    for i in range(n):
        db.add(
            CtiItem(
                task="rcm",
                external_id=f"CVE-{i}",
                source="cve",
                input_text="desc",
                label={"cwe": ["CWE-89"]},
                first_available_date=date(2023, 11, 1),
                status="active",
            )
        )
    db.flush()


def _infer(model_name, prompt, **kw):
    return "Answer: CWE-89"


class TestSliceLimit:
    def test_slice_for_task_respects_limit(self, db_session):
        m = _model(db_session)
        _items(db_session, 5)
        sliced = CtiItemRepository(db_session).slice_for_task("rcm", limit=3)
        assert len(sliced) == 3


class TestPerRunCap:
    def test_caps_inference_calls_at_max_items(self, db_session):
        m = _model(db_session)
        _items(db_session, 10)
        run = CtiRun(model_id=m.id, task="rcm", status="running")
        db_session.add(run)
        db_session.flush()

        calls = []

        def spy(model_name, prompt, **kw):
            calls.append(prompt)
            return "Answer: CWE-89"

        execute_cti_run(str(run.id), m.name, "rcm", db_session, infer=spy, max_items=4)
        assert len(calls) == 4
        assert db_session.query(CtiResult).filter(CtiResult.run_id == run.id).count() == 4


class TestResumePastCap:
    def test_resume_scores_next_slice_when_first_page_already_scored(self, db_session):
        m = _model(db_session)
        _items(db_session, 6)
        run = CtiRun(model_id=m.id, task="rcm", status="running")
        db_session.add(run)
        db_session.flush()

        # Pre-score the first capped page (the 3 oldest items) for THIS run.
        first_page = (
            CtiItemRepository(db_session)
            .slice_for_task("rcm", limit=3)
        )
        for item in first_page:
            db_session.add(
                CtiResult(run_id=run.id, item_id=item.id, model_id=m.id, score=1.0)
            )
        db_session.flush()

        calls = []

        def spy(model_name, prompt, **kw):
            calls.append(prompt)
            return "Answer: CWE-89"

        execute_cti_run(str(run.id), m.name, "rcm", db_session, infer=spy, max_items=3)
        # Must advance to the next 3 unscored items, not re-see the scored first page.
        assert len(calls) == 3
        assert db_session.query(CtiResult).filter(CtiResult.run_id == run.id).count() == 6


class TestIncrementalCommit:
    def test_results_committed_in_batches(self, db_session):
        m = _model(db_session)
        _items(db_session, 6)
        run = CtiRun(model_id=m.id, task="rcm", status="running")
        db_session.add(run)
        db_session.flush()

        # commit_every=2 should leave nothing uncommitted at the end and persist all rows
        execute_cti_run(
            str(run.id), m.name, "rcm", db_session, infer=_infer, commit_every=2
        )
        assert db_session.query(CtiResult).filter(CtiResult.run_id == run.id).count() == 6
