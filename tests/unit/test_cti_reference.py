"""Tests for ATE/TAA scoring and the attack/galaxy reference connectors (Step 4)."""

import pytest

from glokta.application.cti.reference_service import (
    build_taa_indices,
    build_technique_index,
    upsert_attack_techniques,
    upsert_threat_actors,
)
from glokta.domain.cti.scoring import score_ate, score_taa
from glokta.infrastructure.cti.connectors.attack import normalise_attack_bundle
from glokta.infrastructure.cti.connectors.galaxy import normalise_galaxy
from glokta.infrastructure.db.orm import CtiAttackTechnique, CtiThreatActor


# --- scoring -----------------------------------------------------------------


class TestScoreAte:
    def test_exact_set_match(self):
        score, _ = score_ate({"T1059"}, {"T1059"})
        assert score == 1.0

    def test_partial_overlap(self):
        score, bd = score_ate({"T1059", "T1003"}, {"T1059"})
        assert bd["recall"] == 1.0
        assert 0.0 < score < 1.0


class TestScoreTaa:
    ALIAS = {
        "apt29": "APT29",
        "cozy bear": "APT29",
        "the dukes": "APT29",
        "apt28": "APT28",
        "fancy bear": "APT28",
    }
    RELATED = {"APT29": {"APT28"}, "APT28": {"APT29"}}

    def test_alias_match_is_correct(self):
        score, bd = score_taa("Cozy Bear", "APT29", self.ALIAS, self.RELATED)
        assert score == 1.0
        assert bd["result"] == "C"

    def test_related_group_is_plausible(self):
        score, bd = score_taa("APT28", "APT29", self.ALIAS, self.RELATED)
        assert score == 0.5
        assert bd["result"] == "P"

    def test_unrelated_is_independent(self):
        score, bd = score_taa("Lazarus", "APT29", self.ALIAS, self.RELATED)
        assert score == 0.0
        assert bd["result"] == "I"

    def test_none_prediction_is_independent(self):
        score, _ = score_taa(None, "APT29", self.ALIAS, self.RELATED)
        assert score == 0.0


# --- attack connector --------------------------------------------------------

ATTACK_BUNDLE = {
    "objects": [
        {
            "type": "attack-pattern",
            "id": "attack-pattern--1",
            "name": "Command and Scripting Interpreter",
            "external_references": [{"source_name": "mitre-attack", "external_id": "T1059"}],
            "kill_chain_phases": [{"kill_chain_name": "mitre-attack", "phase_name": "execution"}],
        },
        {
            "type": "attack-pattern",
            "id": "attack-pattern--2",
            "name": "Old Technique",
            "revoked": True,
            "external_references": [{"source_name": "mitre-attack", "external_id": "T1064"}],
        },
        {
            "type": "relationship",
            "relationship_type": "revoked-by",
            "source_ref": "attack-pattern--2",
            "target_ref": "attack-pattern--1",
        },
    ]
}


class TestAttackConnector:
    def test_parses_techniques_with_tactic(self):
        techs = normalise_attack_bundle(ATTACK_BUNDLE)
        t1059 = next(t for t in techs if t.technique_id == "T1059")
        assert t1059.name == "Command and Scripting Interpreter"
        assert t1059.tactic == "execution"

    def test_resolves_revoked_by(self):
        techs = normalise_attack_bundle(ATTACK_BUNDLE)
        t1064 = next(t for t in techs if t.technique_id == "T1064")
        assert t1064.revoked_by == "T1059"

    def test_upsert_and_technique_index(self, db_session):
        upsert_attack_techniques(db_session, normalise_attack_bundle(ATTACK_BUNDLE))
        assert db_session.query(CtiAttackTechnique).count() == 2
        index = build_technique_index(db_session)
        assert index["T1064"] == "T1059"  # revoked -> current
        assert index["T1059"] == "T1059"


# --- galaxy connector --------------------------------------------------------

GALAXY = {
    "values": [
        {
            "uuid": "u-29",
            "value": "APT29",
            "meta": {"synonyms": ["Cozy Bear", "The Dukes"]},
            "related": [{"dest-uuid": "u-28", "type": "similar"}],
        },
        {"uuid": "u-28", "value": "APT28", "meta": {"synonyms": ["Fancy Bear"]}},
    ]
}


class TestGalaxyConnector:
    def test_parses_aliases_and_related(self):
        actors = normalise_galaxy(GALAXY)
        apt29 = next(a for a in actors if a.canonical_name == "APT29")
        assert "Cozy Bear" in apt29.aliases
        assert apt29.related_groups == ["APT28"]

    def test_upsert_and_build_indices(self, db_session):
        upsert_threat_actors(db_session, normalise_galaxy(GALAXY))
        assert db_session.query(CtiThreatActor).count() == 2
        alias_index, related_index = build_taa_indices(db_session)
        assert alias_index["cozy bear"] == "APT29"
        assert "APT28" in related_index["APT29"]
        # the indices drive TAA scoring end to end
        score, _ = score_taa("The Dukes", "APT29", alias_index, related_index)
        assert score == pytest.approx(1.0)
