"""Tests for the CTI eval service (application/cti/eval_service.py).

Inference is injected as a fake callable so the slicing, scoring, persistence,
pre/post-cutoff tagging, prequential aggregation, and resume behaviour are testable
without network access.
"""

from datetime import date

from glokta.application.cti.eval_service import execute_cti_run, process_pending_cti_run
from glokta.infrastructure.db.orm import CtiItem, CtiResult, CtiRun, Model


def _seed_model(db, cutoff_end=date(2024, 1, 1)):
    model = Model(
        name="openrouter/test/model",
        provider="test",
        snapshot_date=date(2024, 1, 1),
        cutoff_start=date(2023, 10, 1),
        cutoff_end=cutoff_end,
    )
    db.add(model)
    db.flush()
    return model


def _seed_item(db, external_id, fad, *, withhold=False, label=None):
    item = CtiItem(
        task="rcm",
        external_id=external_id,
        source="cve",
        input_text="A SQL injection ...",
        label=label or {"cwe": ["CWE-89"]},
        first_available_date=fad,
        withhold=withhold,
        status="active",
    )
    db.add(item)
    db.flush()
    return item


def _seed_run(db, model, status="running"):
    run = CtiRun(model_id=model.id, task="rcm", status=status)
    db.add(run)
    db.flush()
    return run


def _fake_infer_correct(model_name, prompt, **kwargs):
    return "Answer: CWE-89"


class TestExecuteCtiRun:
    def test_scores_all_sliced_items_and_persists_results(self, db_session):
        model = _seed_model(db_session)
        _seed_item(db_session, "CVE-1", date(2023, 11, 1))
        _seed_item(db_session, "CVE-2", date(2024, 6, 1))
        run = _seed_run(db_session, model)

        execute_cti_run(str(run.id), model.name, "rcm", db_session, infer=_fake_infer_correct)

        results = db_session.query(CtiResult).filter(CtiResult.run_id == run.id).all()
        assert len(results) == 2
        assert all(r.score == 1.0 for r in results)
        db_session.refresh(run)
        assert run.item_count == 2
        assert run.scored_count == 2

    def test_pre_and_post_cutoff_tagging(self, db_session):
        model = _seed_model(db_session, cutoff_end=date(2024, 1, 1))
        pre = _seed_item(db_session, "CVE-pre", date(2023, 11, 1))
        post = _seed_item(db_session, "CVE-post", date(2024, 6, 1))
        run = _seed_run(db_session, model)

        execute_cti_run(str(run.id), model.name, "rcm", db_session, infer=_fake_infer_correct)

        by_item = {
            r.item_id: r for r in db_session.query(CtiResult).filter(CtiResult.run_id == run.id)
        }
        assert by_item[pre.id].pre_cutoff is True
        assert by_item[post.id].pre_cutoff is False

    def test_withheld_items_excluded(self, db_session):
        model = _seed_model(db_session)
        _seed_item(db_session, "CVE-1", date(2023, 11, 1))
        _seed_item(db_session, "CVE-withheld", date(2024, 6, 1), withhold=True)
        run = _seed_run(db_session, model)

        execute_cti_run(str(run.id), model.name, "rcm", db_session, infer=_fake_infer_correct)

        results = db_session.query(CtiResult).filter(CtiResult.run_id == run.id).all()
        assert len(results) == 1

    def test_prequential_score_recorded(self, db_session):
        model = _seed_model(db_session)
        _seed_item(db_session, "CVE-1", date(2023, 11, 1))
        run = _seed_run(db_session, model)

        execute_cti_run(str(run.id), model.name, "rcm", db_session, infer=_fake_infer_correct)
        db_session.refresh(run)
        assert run.prequential_score == 1.0

    def test_resume_skips_already_scored_items(self, db_session):
        model = _seed_model(db_session)
        item = _seed_item(db_session, "CVE-1", date(2023, 11, 1))
        run = _seed_run(db_session, model)
        # Pre-existing result for this item — must not be re-scored.
        db_session.add(
            CtiResult(run_id=run.id, item_id=item.id, model_id=model.id, score=0.0)
        )
        db_session.flush()

        calls: list[str] = []

        def _spy_infer(model_name, prompt, **kwargs):
            calls.append(prompt)
            return "Answer: CWE-89"

        execute_cti_run(str(run.id), model.name, "rcm", db_session, infer=_spy_infer)
        assert calls == []  # nothing new to score


class TestProcessPendingCtiRun:
    def test_transitions_pending_run_to_complete(self, db_session):
        model = _seed_model(db_session)
        _seed_item(db_session, "CVE-1", date(2023, 11, 1))
        run = _seed_run(db_session, model, status="pending")

        process_pending_cti_run(
            db_session,
            lambda run_id, model_name, task: execute_cti_run(
                run_id, model_name, task, db_session, infer=_fake_infer_correct
            ),
        )
        db_session.refresh(run)
        assert run.status == "complete"
        assert run.started_at is not None
        assert run.completed_at is not None
