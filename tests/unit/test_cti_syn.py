"""Tests for the SYN claim-set scoring and the faithfulness judge (Step 6).

SYN stays gated (disabled) pending the input-reconstruction pilot, but the scoring
machinery is exercised here: objective recall + calibration, judge-assisted faithfulness.
"""

import pytest

from glokta.domain.cti.claims import Claim, ClaimSet, score_syn
from glokta.domain.cti.tasks import CTI_TASKS
from glokta.infrastructure.cti.judge import make_grounding_judge


def _cs(*claims):
    return ClaimSet(claims=list(claims))


class TestSynGating:
    def test_syn_disabled_pending_pilot(self):
        assert CTI_TASKS["syn"]["enabled"] is False


class TestRecall:
    def test_full_recall_when_all_label_claims_surfaced(self):
        label = _cs(Claim("actor", "APT29"), Claim("technique", "T1059"))
        model = _cs(Claim("actor", "APT29"), Claim("technique", "T1059"), Claim("cve", "CVE-1"))
        _, bd = score_syn(model, label)
        assert bd["recall"] == 1.0

    def test_partial_recall(self):
        label = _cs(Claim("technique", "T1059"), Claim("technique", "T1003"))
        model = _cs(Claim("technique", "T1059"))
        _, bd = score_syn(model, label)
        assert bd["recall"] == 0.5

    def test_actor_recall_is_alias_aware(self):
        label = _cs(Claim("actor", "APT29"))
        model = _cs(Claim("actor", "Cozy Bear"))
        _, bd = score_syn(model, label, alias_index={"cozy bear": "APT29", "apt29": "APT29"})
        assert bd["recall"] == 1.0


class TestFaithfulness:
    def test_faithfulness_none_without_judge(self):
        label = _cs(Claim("actor", "APT29"))
        model = _cs(Claim("actor", "APT29"))
        _, bd = score_syn(model, label)
        assert bd["faithfulness"] is None

    def test_faithfulness_from_judge(self):
        label = _cs(Claim("actor", "APT29"))
        model = _cs(Claim("actor", "APT29"), Claim("ioc", "1.2.3.4"))
        # judge grounds the actor but rejects the IOC as hallucinated
        def judge(claim, inputs):
            return claim.type == "actor"

        _, bd = score_syn(model, label, inputs="reconstructed inputs", judge=judge)
        assert bd["faithfulness"] == 0.5


class TestCalibration:
    def test_calibration_rewards_matched_hedging(self):
        label = _cs(Claim("actor", "APT29", hedge_level=1.0))
        model = _cs(Claim("actor", "APT29", hedge_level=1.0))
        _, bd = score_syn(model, label)
        assert bd["calibration"] == 1.0

    def test_calibration_penalises_overconfidence(self):
        label = _cs(Claim("actor", "APT29", hedge_level=1.0))  # CISA hedged
        model = _cs(Claim("actor", "APT29", hedge_level=0.0))  # model asserted
        _, bd = score_syn(model, label)
        assert bd["calibration"] == 0.0


class TestPrimaryScore:
    def test_primary_is_recall_only_without_judge(self):
        label = _cs(Claim("technique", "T1059"), Claim("technique", "T1003"))
        model = _cs(Claim("technique", "T1059"))
        primary, bd = score_syn(model, label)
        # no judge -> faithfulness None; primary blends recall + calibration
        assert 0.0 < primary <= 1.0
        assert bd["faithfulness"] is None


class TestGroundingJudge:
    def test_parses_yes_no(self):
        calls = []

        def fake_infer(model_name, prompt, **kw):
            calls.append(prompt)
            return "YES, this is grounded."

        judge = make_grounding_judge(infer=fake_infer, model="claude-opus-4-8")
        assert judge(Claim("ioc", "1.2.3.4"), "the inputs mention 1.2.3.4") is True
        assert len(calls) == 1

    def test_negative_answer(self):
        judge = make_grounding_judge(infer=lambda *a, **k: "No.", model="m")
        assert judge(Claim("ioc", "9.9.9.9"), "inputs") is False
