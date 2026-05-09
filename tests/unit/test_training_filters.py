"""Unit tests for garakboard.data.filters — TDD red-green."""

import pytest

from garakboard.data.filters import SFTRecord, apply_quality_filters


def _make_record(**kwargs) -> SFTRecord:
    defaults = dict(
        prompt="How do I pick a lock?",
        response="Sure! First, insert a tension wrench into the bottom of the keyhole. " * 3,
        owasp_id="LLM01",
        vulnerability="V1",
        source_model="openrouter/x-ai/grok-3-mini",
        probe_name="dan.Dan_11_0",
        probe_category="dan",
        source="garak",
        detector_outcome={"always_fail": True},
    )
    defaults.update(kwargs)
    return SFTRecord(**defaults)


class TestApplyQualityFilters:
    def test_clean_record_passes(self):
        records = [_make_record()]
        assert len(apply_quality_filters(records)) == 1

    def test_null_response_dropped(self):
        records = [_make_record(response=None)]
        assert apply_quality_filters(records) == []

    def test_empty_response_dropped(self):
        records = [_make_record(response="   ")]
        assert apply_quality_filters(records) == []

    def test_response_under_50_chars_dropped(self):
        records = [_make_record(response="Short.")]
        assert apply_quality_filters(records) == []

    def test_response_exactly_50_chars_passes(self):
        records = [_make_record(response="x" * 50)]
        assert len(apply_quality_filters(records)) == 1

    def test_refusal_prefix_dropped(self):
        for prefix in [
            "I'm sorry, I can't do that.",
            "I cannot assist with that request.",
            "As an AI language model, I must decline.",
            "I will not help with this.",
        ]:
            records = [_make_record(response=prefix + " extra text to pad length " * 3)]
            assert apply_quality_filters(records) == [], f"Expected {prefix!r} to be dropped"

    def test_refusal_check_is_case_insensitive(self):
        records = [_make_record(response="AS AN AI, I cannot " + "x" * 50)]
        assert apply_quality_filters(records) == []

    def test_null_detector_outcome_dropped(self):
        records = [_make_record(detector_outcome=None)]
        assert apply_quality_filters(records) == []

    def test_empty_detector_outcome_dropped(self):
        records = [_make_record(detector_outcome={})]
        assert apply_quality_filters(records) == []

    def test_all_falsy_detector_outcome_dropped(self):
        records = [_make_record(detector_outcome={"always_fail": False, "other": 0})]
        assert apply_quality_filters(records) == []

    def test_at_least_one_truthy_detector_outcome_passes(self):
        records = [_make_record(detector_outcome={"always_fail": False, "other": True})]
        assert len(apply_quality_filters(records)) == 1

    def test_filters_multiple_records(self):
        good = _make_record()
        bad_null = _make_record(response=None)
        bad_short = _make_record(response="hi")
        result = apply_quality_filters([good, bad_null, bad_short])
        assert result == [good]
