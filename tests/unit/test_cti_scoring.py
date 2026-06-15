"""Unit tests for pure CTI scoring functions (domain/cti/scoring.py).

These are deliberately I/O-free so they form the fast, high-confidence TDD core
of the CTI benchmark (build-order step 1).
"""

from datetime import date

import pytest

from glokta.domain.cti.scoring import (
    prequential_accuracy,
    score_forecast,
    score_rcm,
    score_vsp,
)


class TestScoreRcm:
    """CTI-RCM: CVE description -> CWE id(s), scored by set F1."""

    def test_exact_single_match_is_perfect(self):
        score, breakdown = score_rcm({"CWE-79"}, {"CWE-79"})
        assert score == 1.0
        assert breakdown["precision"] == 1.0
        assert breakdown["recall"] == 1.0

    def test_normalises_case_and_whitespace(self):
        score, _ = score_rcm({" cwe-79 "}, {"CWE-79"})
        assert score == 1.0

    def test_partial_overlap_uses_f1(self):
        # predicted {79, 89}, labels {79} -> precision 0.5, recall 1.0, F1 = 2/3
        score, breakdown = score_rcm({"CWE-79", "CWE-89"}, {"CWE-79"})
        assert breakdown["precision"] == 0.5
        assert breakdown["recall"] == 1.0
        assert score == pytest.approx(2 / 3)

    def test_no_overlap_is_zero(self):
        score, _ = score_rcm({"CWE-22"}, {"CWE-79"})
        assert score == 0.0

    def test_both_empty_is_perfect(self):
        score, _ = score_rcm(set(), set())
        assert score == 1.0

    def test_prediction_empty_but_label_present_is_zero(self):
        score, _ = score_rcm(set(), {"CWE-79"})
        assert score == 0.0


class TestScoreVsp:
    """CTI-VSP: CVE description -> CVSS vector, scored by base-score MAD."""

    CRITICAL = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"  # base 9.8, Critical

    def test_identical_vector_is_perfect(self):
        score, breakdown = score_vsp(self.CRITICAL, self.CRITICAL)
        assert score == 1.0
        assert breakdown["mad"] == 0.0
        assert breakdown["severity_match"] is True
        assert breakdown["component_distance"] == 0.0

    def test_accuracy_is_one_minus_mad_over_denominator(self):
        # label base 9.8; predict a low-severity vector and check 1 - mad/7.7
        low = "CVSS:3.1/AV:N/AC:H/PR:H/UI:R/S:U/C:N/I:N/A:L"
        score, breakdown = score_vsp(low, self.CRITICAL, denominator=7.7)
        expected = max(0.0, 1.0 - breakdown["mad"] / 7.7)
        assert score == pytest.approx(expected)
        assert breakdown["mad"] > 0.0
        assert breakdown["severity_match"] is False

    def test_unparseable_prediction_scores_against_full_label_base(self):
        score, breakdown = score_vsp("not-a-vector", self.CRITICAL, denominator=7.7)
        assert breakdown["mad"] == pytest.approx(breakdown["label_base"])
        assert score == pytest.approx(max(0.0, 1.0 - breakdown["label_base"] / 7.7))

    def test_none_prediction_scores_against_full_label_base(self):
        score, breakdown = score_vsp(None, self.CRITICAL)
        assert breakdown["pred_base"] is None
        assert score < 1.0

    def test_component_distance_counts_differing_base_metrics(self):
        # flip a single base metric (AV:N -> AV:L) => 1/8 metrics differ
        flipped = "CVSS:3.1/AV:L/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"
        _, breakdown = score_vsp(flipped, self.CRITICAL)
        assert breakdown["component_distance"] == pytest.approx(1 / 8)


class TestScoreForecast:
    """Forecast: P(exploited) vs KEV-resolved outcome, scored by Brier."""

    def test_confident_correct_positive_is_zero_brier(self):
        brier, breakdown = score_forecast(1.0, resolved=True)
        assert brier == 0.0
        assert breakdown["outcome"] == 1.0

    def test_confident_wrong_is_max_brier(self):
        brier, _ = score_forecast(1.0, resolved=False)
        assert brier == 1.0

    def test_uncertain_prediction(self):
        brier, _ = score_forecast(0.5, resolved=True)
        assert brier == pytest.approx(0.25)

    def test_probability_is_clamped_to_unit_interval(self):
        brier, _ = score_forecast(1.5, resolved=True)
        assert brier == 0.0


class TestPrequentialAccuracy:
    """Gama et al. fading-factor accuracy over a recency-ordered series."""

    def test_uniform_scores_average_to_that_score(self):
        series = [(date(2024, 1, i + 1), 1.0) for i in range(5)]
        assert prequential_accuracy(series, fading_factor=0.9) == pytest.approx(1.0)

    def test_recent_results_weigh_more(self):
        # old failures, recent successes -> weighted mean above the plain mean (0.5)
        series = [
            (date(2024, 1, 1), 0.0),
            (date(2024, 1, 2), 0.0),
            (date(2024, 1, 3), 1.0),
            (date(2024, 1, 4), 1.0),
        ]
        weighted = prequential_accuracy(series, fading_factor=0.5)
        assert weighted > 0.5

    def test_unsorted_input_is_ordered_by_date(self):
        ordered = [
            (date(2024, 1, 1), 0.0),
            (date(2024, 1, 2), 1.0),
        ]
        shuffled = [ordered[1], ordered[0]]
        assert prequential_accuracy(shuffled, 0.5) == pytest.approx(
            prequential_accuracy(ordered, 0.5)
        )

    def test_fading_factor_of_one_is_plain_mean(self):
        series = [(date(2024, 1, 1), 0.0), (date(2024, 1, 2), 1.0)]
        assert prequential_accuracy(series, fading_factor=1.0) == pytest.approx(0.5)

    def test_empty_series_is_zero(self):
        assert prequential_accuracy([], 0.9) == 0.0
