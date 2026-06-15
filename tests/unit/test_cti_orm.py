"""Round-trip tests for the CTI ORM tables (build-order step 1)."""

import uuid
from datetime import date, datetime, timezone

from glokta.infrastructure.db.orm import (
    CtiAttackTechnique,
    CtiItem,
    CtiKev,
    CtiResult,
    CtiRun,
    CtiThreatActor,
    Model,
)


def _make_model(db, name="openrouter/test/model"):
    model = Model(
        name=name,
        provider="test",
        snapshot_date=date(2024, 1, 1),
        cutoff_start=date(2023, 10, 1),
        cutoff_end=date(2024, 1, 1),
    )
    db.add(model)
    db.flush()
    return model


class TestModelCutoffColumns:
    def test_model_stores_cutoff_range(self, db_session):
        model = _make_model(db_session)
        db_session.refresh(model)
        assert model.cutoff_start == date(2023, 10, 1)
        assert model.cutoff_end == date(2024, 1, 1)

    def test_cutoff_is_optional(self, db_session):
        model = Model(
            name="openrouter/test/no-cutoff",
            provider="test",
            snapshot_date=date(2024, 1, 1),
        )
        db_session.add(model)
        db_session.flush()
        assert model.cutoff_start is None
        assert model.cutoff_end is None


class TestCtiItem:
    def test_round_trip_with_temporal_and_provenance_fields(self, db_session):
        item = CtiItem(
            task="rcm",
            external_id="CVE-2024-1234",
            source="cve",
            input_text="A heap overflow in foo...",
            label={"cwe": ["CWE-122"]},
            label_provenance={"cna": ["CWE-122"], "adp": ["CWE-122"]},
            source_revision="abc123",
            input_date=date(2024, 5, 1),
            label_date=date(2024, 5, 15),
            first_available_date=date(2024, 5, 15),
            authority_agreement="agree",
            difficulty={"cwe_class": "memory", "description_length": 25},
            withhold=False,
            status="active",
        )
        db_session.add(item)
        db_session.flush()
        db_session.refresh(item)
        assert isinstance(item.id, uuid.UUID)
        assert item.label["cwe"] == ["CWE-122"]
        assert item.first_available_date == date(2024, 5, 15)
        assert item.withhold is False


class TestCtiRunAndResult:
    def test_run_and_result_round_trip(self, db_session):
        model = _make_model(db_session)
        item = CtiItem(
            task="vsp",
            external_id="CVE-2024-9999",
            source="cve",
            input_text="desc",
            label={"vector": "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"},
            first_available_date=date(2024, 6, 1),
        )
        db_session.add(item)
        db_session.flush()

        run = CtiRun(
            model_id=model.id,
            task="vsp",
            triggered_by="scheduled",
            status="running",
            model_cutoff_start=model.cutoff_start,
            model_cutoff_end=model.cutoff_end,
            item_count=1,
        )
        db_session.add(run)
        db_session.flush()

        result = CtiResult(
            run_id=run.id,
            item_id=item.id,
            model_id=model.id,
            prompt="score this CVE",
            prompt_hash="deadbeef",
            response="CVSS:3.1/...",
            parsed_output={"vector": "CVSS:3.1/AV:N/..."},
            score=0.91,
            score_breakdown={"mad": 0.7, "severity_match": True},
            correct=True,
            pre_cutoff=False,
            created_at=datetime.now(timezone.utc),
        )
        db_session.add(result)
        db_session.flush()
        db_session.refresh(result)

        assert result.score == 0.91
        assert result.pre_cutoff is False
        assert result.run_id == run.id
        assert result.item_id == item.id


class TestReferenceTables:
    def test_attack_technique_round_trip(self, db_session):
        tech = CtiAttackTechnique(
            technique_id="T1059", name="Command and Scripting Interpreter", tactic="execution"
        )
        db_session.add(tech)
        db_session.flush()
        db_session.refresh(tech)
        assert tech.technique_id == "T1059"

    def test_threat_actor_round_trip(self, db_session):
        actor = CtiThreatActor(
            canonical_name="APT29",
            aliases=["Cozy Bear", "The Dukes"],
            related_groups=["APT28"],
        )
        db_session.add(actor)
        db_session.flush()
        db_session.refresh(actor)
        assert "Cozy Bear" in actor.aliases

    def test_kev_round_trip(self, db_session):
        kev = CtiKev(cve_id="CVE-2024-1234", date_added=date(2024, 7, 1), vendor="Acme")
        db_session.add(kev)
        db_session.flush()
        db_session.refresh(kev)
        assert kev.cve_id == "CVE-2024-1234"
