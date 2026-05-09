"""Unit tests for assess_garak_dataset threshold and readiness logic — TDD red-green."""

import sys
import os

import pytest

# scripts/ is not a package; import by path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))

from assess_garak_dataset import (
    readiness_label,
    readiness_exit_code,
    hit_rate_label,
    deduplicate_prompts,
)


class TestReadinessLabel:
    def test_zero_hits_is_critical(self):
        assert "CRITICAL" in readiness_label(0)

    def test_99_hits_is_critical(self):
        assert "CRITICAL" in readiness_label(99)

    def test_100_hits_is_marginal(self):
        assert "MARGINAL" in readiness_label(100)

    def test_499_hits_is_marginal(self):
        assert "MARGINAL" in readiness_label(499)

    def test_500_hits_is_sufficient(self):
        assert "SUFFICIENT" in readiness_label(500)

    def test_2999_hits_is_sufficient(self):
        assert "SUFFICIENT" in readiness_label(2999)

    def test_3000_hits_is_target_reached(self):
        assert "TARGET REACHED" in readiness_label(3000)

    def test_5000_hits_is_target_reached(self):
        assert "TARGET REACHED" in readiness_label(5000)


class TestReadinessExitCode:
    def test_critical_exits_2(self):
        assert readiness_exit_code(0) == 2
        assert readiness_exit_code(99) == 2

    def test_below_target_exits_1(self):
        assert readiness_exit_code(100) == 1
        assert readiness_exit_code(2999) == 1

    def test_target_reached_exits_0(self):
        assert readiness_exit_code(3000) == 0
        assert readiness_exit_code(9999) == 0


class TestHitRateLabel:
    def test_below_10_is_fail(self):
        assert "FAIL" in hit_rate_label(9.9)

    def test_10_to_14_is_warn(self):
        assert "WARN" in hit_rate_label(10.0)
        assert "WARN" in hit_rate_label(14.9)

    def test_15_and_above_is_pass(self):
        assert "PASS" in hit_rate_label(15.0)
        assert "PASS" in hit_rate_label(50.0)


class TestDeduplicatePrompts:
    def test_dedup_exact_duplicates(self):
        prompts = ["hello world", "foo bar", "hello world"]
        assert deduplicate_prompts(prompts) == 2

    def test_dedup_strips_whitespace(self):
        prompts = ["  hello  ", "hello"]
        assert deduplicate_prompts(prompts) == 1

    def test_empty_list(self):
        assert deduplicate_prompts([]) == 0

    def test_all_unique(self):
        assert deduplicate_prompts(["a", "b", "c"]) == 3
