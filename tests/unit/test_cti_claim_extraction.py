"""Tests for SYN claim-set extraction, input reconstruction, and the syn evaluator branch."""

import json

from glokta.infrastructure.cti.claim_extraction import (
    build_claim_set,
    claim_set_from_text,
    reconstruct_inputs,
)
from glokta.infrastructure.cti.evaluator import evaluate_item


ADVISORY = {
    "id": "AA24-100A",
    "actor": "APT29",
    "cves": ["CVE-2024-1234"],
    "techniques": ["T1059", "T1566"],
    "sections": {
        "Summary": "APT29 attribution and conclusions.",
        "Technical Details": "Observed beaconing to 1.2.3.4.",
        "Indicators of Compromise": "1.2.3.4\n" + "a" * 64,
        "Mitigations": "Patch and segment networks.",
    },
}


class TestBuildClaimSetDeterministic:
    def test_extracts_actor_cve_technique_ioc(self):
        cs = build_claim_set(ADVISORY)
        types = {c.type for c in cs.claims}
        assert {"actor", "cve", "technique", "ioc"} <= types
        values = {c.value for c in cs.claims}
        assert "APT29" in values and "CVE-2024-1234" in values and "1.2.3.4" in values

    def test_no_judge_means_no_sectors_or_mitigations(self):
        cs = build_claim_set(ADVISORY)
        assert not any(c.type in {"sector", "mitigation"} for c in cs.claims)


class TestBuildClaimSetWithJudge:
    def test_judge_adds_sectors_mitigations_and_hedges(self):
        def fake_judge(model, prompt):
            return json.dumps(
                {
                    "sectors": ["energy"],
                    "mitigations": ["patch"],
                    "hedges": {"APT29": 1.0},
                }
            )

        cs = build_claim_set(ADVISORY, judge_infer=fake_judge)
        assert any(c.type == "sector" and c.value == "energy" for c in cs.claims)
        assert any(c.type == "mitigation" for c in cs.claims)
        actor = next(c for c in cs.claims if c.type == "actor")
        assert actor.hedge_level == 1.0


class TestReconstructInputs:
    def test_keeps_observations_drops_conclusions(self):
        text = reconstruct_inputs(ADVISORY)
        assert "beaconing to 1.2.3.4" in text  # Technical Details kept
        assert "attribution and conclusions" not in text  # Summary dropped
        assert "Patch and segment" not in text  # Mitigations dropped


class TestClaimSetFromText:
    def test_extracts_from_model_free_text(self):
        cs = claim_set_from_text(
            "This looks like Cozy Bear exploiting CVE-2024-1234 via T1059.",
            alias_index={"cozy bear": "APT29"},
        )
        values = {(c.type, c.value) for c in cs.claims}
        assert ("actor", "APT29") in values
        assert ("cve", "CVE-2024-1234") in values
        assert ("technique", "T1059") in values


class TestSynEvaluator:
    def test_recall_and_faithfulness_scored(self):
        label = {
            "claims": [
                {"type": "actor", "value": "APT29", "hedge_level": 0.0},
                {"type": "technique", "value": "T1059", "hedge_level": 0.0},
            ]
        }
        response = "Attributed to APT29 using T1059."
        context = {
            "alias_index": {"apt29": "APT29"},
            "inputs": "reconstructed inputs",
            "judge": lambda claim, inputs: True,  # everything grounded
        }
        scored = evaluate_item("syn", label, response, context)
        assert scored.breakdown["recall"] == 1.0
        assert scored.breakdown["faithfulness"] == 1.0
        assert scored.correct is True
