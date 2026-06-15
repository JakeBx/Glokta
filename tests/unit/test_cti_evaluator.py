"""Tests for the pure per-item evaluator (infrastructure/cti/evaluator.py)."""

from glokta.infrastructure.cti.evaluator import evaluate_item

VEC = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"


class TestEvaluateItemRcm:
    def test_exact_match_is_correct(self):
        scored = evaluate_item("rcm", {"cwe": ["CWE-79"]}, "Answer: CWE-79")
        assert scored.score == 1.0
        assert scored.correct is True
        assert scored.parsed_output == {"cwe": ["CWE-79"]}

    def test_partial_match_not_correct(self):
        scored = evaluate_item("rcm", {"cwe": ["CWE-79"]}, "CWE-79 and CWE-89")
        assert scored.correct is False
        assert 0.0 < scored.score < 1.0


class TestEvaluateItemVsp:
    def test_exact_vector_is_correct(self):
        scored = evaluate_item("vsp", {"vector": VEC}, f"Answer: {VEC}")
        assert scored.score == 1.0
        assert scored.correct is True
        assert scored.parsed_output == {"vector": VEC}

    def test_wrong_severity_not_correct(self):
        low = "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:N/I:N/A:L"
        scored = evaluate_item("vsp", {"vector": VEC}, f"Answer: {low}")
        assert scored.correct is False
        assert scored.score < 1.0

    def test_unparseable_response_scores_low(self):
        scored = evaluate_item("vsp", {"vector": VEC}, "I cannot tell")
        assert scored.parsed_output == {"vector": None}
        assert scored.correct is False
