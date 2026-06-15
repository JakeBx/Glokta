"""Unit tests for CTI prompt templates and answer extractors.

The extraction strategy is ported from athenabench: strip answer-label prefixes,
scan bottom-to-top (the model's final answer is usually last), per-task regex.
"""

from glokta.infrastructure.cti.prompts import (
    build_prompt,
    parse_response,
    prompt_hash,
)


class TestExtractCwes:
    def test_single_cwe(self):
        assert parse_response("rcm", "The answer is CWE-79.") == {"CWE-79"}

    def test_multiple_cwes_on_one_line(self):
        assert parse_response("rcm", "Likely CWE-79 and CWE-89") == {"CWE-79", "CWE-89"}

    def test_prefers_final_answer_line_bottom_to_top(self):
        resp = "Reasoning mentions CWE-200 exposure.\nAnswer: CWE-79"
        assert parse_response("rcm", resp) == {"CWE-79"}

    def test_case_insensitive_normalised_to_upper(self):
        assert parse_response("rcm", "answer: cwe-79") == {"CWE-79"}

    def test_no_match_returns_empty_set(self):
        assert parse_response("rcm", "no weakness identified") == set()


class TestExtractCvssVector:
    VEC = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H"

    def test_extracts_vector(self):
        assert parse_response("vsp", f"Answer: {self.VEC}") == self.VEC

    def test_strips_trailing_punctuation(self):
        assert parse_response("vsp", f"The vector is {self.VEC}.") == self.VEC

    def test_prefers_last_vector(self):
        other = "CVSS:3.1/AV:L/AC:H/PR:H/UI:R/S:U/C:L/I:L/A:L"
        resp = f"Maybe {other}\nFinal answer: {self.VEC}"
        assert parse_response("vsp", resp) == self.VEC

    def test_no_vector_returns_none(self):
        assert parse_response("vsp", "I cannot determine the score") is None


class TestBuildPrompt:
    def test_rcm_prompt_includes_description_and_cwe(self):
        prompt = build_prompt("rcm", "A buffer overflow in foo")
        assert "A buffer overflow in foo" in prompt
        assert "CWE" in prompt

    def test_vsp_prompt_includes_description_and_cvss(self):
        prompt = build_prompt("vsp", "A buffer overflow in foo")
        assert "A buffer overflow in foo" in prompt
        assert "CVSS" in prompt


class TestPromptHash:
    def test_deterministic_sha256(self):
        h1 = prompt_hash("hello")
        h2 = prompt_hash("hello")
        assert h1 == h2
        assert len(h1) == 64

    def test_different_prompts_differ(self):
        assert prompt_hash("a") != prompt_hash("b")
