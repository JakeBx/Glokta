"""Tests for the report connector and ATE/TAA evaluation wiring (Step 5)."""

from datetime import date

from glokta.application.cti.eval_service import execute_cti_run
from glokta.application.cti.reference_service import upsert_threat_actors
from glokta.infrastructure.cti.connectors.galaxy import normalise_galaxy
from glokta.infrastructure.cti.connectors.report import normalise_report
from glokta.infrastructure.cti.evaluator import evaluate_item
from glokta.infrastructure.cti.prompts import parse_response
from glokta.infrastructure.db.orm import CtiItem, CtiResult, CtiRun, Model


ADVISORY = {
    "id": "AA24-001A",
    "published": "2024-01-10",
    "text": "The actors used T1059 and T1003.001 against the victim.",
    "techniques": ["T1059", "T1003"],
    "actor": "APT29",
}


class TestNormaliseReport:
    def test_produces_ate_and_taa_items(self):
        items = normalise_report(ADVISORY)
        assert {i.task for i in items} == {"ate", "taa"}
        ate = next(i for i in items if i.task == "ate")
        assert ate.label == {"techniques": ["T1059", "T1003"]}
        assert ate.input_date == date(2024, 1, 10)
        taa = next(i for i in items if i.task == "taa")
        assert taa.label == {"actor": "APT29"}

    def test_no_labels_yields_nothing(self):
        assert normalise_report({"id": "x", "text": "y"}) == []

    def test_accepts_date_object_for_published(self):
        # parse_advisory emits a date object (not a string) — normalise_report must accept it.
        adv = dict(ADVISORY, published=date(2024, 1, 10))
        items = normalise_report(adv)
        assert items[0].input_date == date(2024, 1, 10)


class TestParsers:
    def test_ate_extracts_top_level_techniques(self):
        assert parse_response("ate", "Seen: T1059 and T1003.001") == {"T1059", "T1003"}

    def test_taa_extracts_actor(self):
        assert parse_response("taa", 'Answer: "APT29"') == "APT29"


class TestEvaluateAteTaa:
    def test_ate_with_revoked_normalisation(self):
        # model says T1064 (revoked); index maps it to T1059 which is the label
        scored = evaluate_item(
            "ate",
            {"techniques": ["T1059"]},
            "T1064",
            context={"technique_index": {"T1064": "T1059", "T1059": "T1059"}},
        )
        assert scored.score == 1.0
        assert scored.correct is True

    def test_taa_alias_match(self):
        scored = evaluate_item(
            "taa",
            {"actor": "APT29"},
            "Cozy Bear",
            context={"alias_index": {"cozy bear": "APT29", "apt29": "APT29"}, "related_index": {}},
        )
        assert scored.score == 1.0
        assert scored.correct is True


GALAXY = {
    "values": [
        {"uuid": "u-29", "value": "APT29", "meta": {"synonyms": ["Cozy Bear"]}},
    ]
}


class TestTaaRunEndToEnd:
    def test_taa_run_uses_galaxy_indices(self, db_session):
        upsert_threat_actors(db_session, normalise_galaxy(GALAXY))
        model = Model(name="openrouter/m", provider="p", snapshot_date=date(2024, 1, 1))
        db_session.add(model)
        db_session.flush()
        db_session.add(
            CtiItem(
                task="taa",
                external_id="AA-1",
                source="report",
                input_text="narrative",
                label={"actor": "APT29"},
                first_available_date=date(2024, 1, 10),
                status="active",
            )
        )
        run = CtiRun(model_id=model.id, task="taa", status="running")
        db_session.add(run)
        db_session.flush()

        execute_cti_run(
            str(run.id),
            model.name,
            "taa",
            db_session,
            infer=lambda model_name, prompt, **kw: "Answer: Cozy Bear",
        )
        result = db_session.query(CtiResult).filter(CtiResult.run_id == run.id).one()
        assert result.score == 1.0
        assert result.correct is True
