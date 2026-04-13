"""Tests for garak JSONL ingest pipeline."""

import json
import pytest

from garakboard.models import ProbeResult, Attempt


# --- parse_eval_entry tests ---

def test_parse_eval_entry_returns_probe_result(db_session):
    """parse_eval_entry returns a ProbeResult instance."""
    from garakboard.ingest.jsonl_parser import parse_eval_entry

    entry = {
        "entry_type": "eval",
        "probe": "encoding.InjectBase64",
        "detector": "always.Fail",
        "passed": 8,
        "failed": 2,
        "score": 0.8,
        "run_id": "garak-run-001",
    }
    run_id = "11111111-1111-1111-1111-111111111111"
    result = parse_eval_entry(entry, run_id)

    assert isinstance(result, ProbeResult)


def test_parse_eval_entry_extracts_probe_category(db_session):
    """probe_category is extracted as the part before the first dot in probe name."""
    from garakboard.ingest.jsonl_parser import parse_eval_entry

    entry = {
        "entry_type": "eval",
        "probe": "encoding.InjectBase64",
        "detector": "always.Fail",
        "passed": 8,
        "failed": 2,
        "score": 0.8,
        "run_id": "garak-run-001",
    }
    run_id = "11111111-1111-1111-1111-111111111111"
    result = parse_eval_entry(entry, run_id)

    assert result.probe_category == "encoding"
    assert result.probe_name == "encoding.InjectBase64"


def test_parse_eval_entry_maps_passed_to_pass_count(db_session):
    """'passed' field in entry maps to pass_count on ProbeResult."""
    from garakboard.ingest.jsonl_parser import parse_eval_entry

    entry = {
        "entry_type": "eval",
        "probe": "encoding.InjectBase64",
        "detector": "always.Fail",
        "passed": 8,
        "failed": 2,
        "score": 0.8,
        "run_id": "garak-run-001",
    }
    run_id = "11111111-1111-1111-1111-111111111111"
    result = parse_eval_entry(entry, run_id)

    assert result.pass_count == 8


def test_parse_eval_entry_maps_failed_to_fail_count(db_session):
    """'failed' field in entry maps to fail_count on ProbeResult."""
    from garakboard.ingest.jsonl_parser import parse_eval_entry

    entry = {
        "entry_type": "eval",
        "probe": "encoding.InjectBase64",
        "detector": "always.Fail",
        "passed": 8,
        "failed": 2,
        "score": 0.8,
        "run_id": "garak-run-001",
    }
    run_id = "11111111-1111-1111-1111-111111111111"
    result = parse_eval_entry(entry, run_id)

    assert result.fail_count == 2


def test_parse_eval_entry_maps_score(db_session):
    """'score' field maps to score on ProbeResult."""
    from garakboard.ingest.jsonl_parser import parse_eval_entry

    entry = {
        "entry_type": "eval",
        "probe": "encoding.InjectBase64",
        "detector": "always.Fail",
        "passed": 8,
        "failed": 2,
        "score": 0.8,
        "run_id": "garak-run-001",
    }
    run_id = "11111111-1111-1111-1111-111111111111"
    result = parse_eval_entry(entry, run_id)

    assert result.score == 0.8


def test_parse_eval_entry_score_can_be_none(db_session):
    """score is None when not present in entry."""
    from garakboard.ingest.jsonl_parser import parse_eval_entry

    entry = {
        "entry_type": "eval",
        "probe": "encoding.InjectBase64",
        "detector": "always.Fail",
        "passed": 8,
        "failed": 2,
        "run_id": "garak-run-001",
    }
    run_id = "11111111-1111-1111-1111-111111111111"
    result = parse_eval_entry(entry, run_id)

    assert result.score is None


def test_parse_eval_entry_wrong_type_raises_value_error(db_session):
    """ValueError raised when entry_type is not 'eval'."""
    from garakboard.ingest.jsonl_parser import parse_eval_entry

    entry = {
        "entry_type": "attempt",
        "probe": "encoding.InjectBase64",
        "detector": "always.Fail",
        "passed": 8,
        "failed": 2,
        "score": 0.8,
        "run_id": "garak-run-001",
    }
    run_id = "11111111-1111-1111-1111-111111111111"

    with pytest.raises(ValueError):
        parse_eval_entry(entry, run_id)


def test_parse_eval_entry_probe_without_dot(db_session):
    """probe_category equals probe_name when probe has no dot."""
    from garakboard.ingest.jsonl_parser import parse_eval_entry

    entry = {
        "entry_type": "eval",
        "probe": "SimpleProbe",
        "detector": "always.Fail",
        "passed": 5,
        "failed": 5,
        "score": 0.5,
        "run_id": "garak-run-001",
    }
    run_id = "11111111-1111-1111-1111-111111111111"
    result = parse_eval_entry(entry, run_id)

    assert result.probe_name == "SimpleProbe"
    assert result.probe_category == "SimpleProbe"


# --- parse_attempt_entry tests ---


def test_parse_attempt_entry_returns_attempt(db_session):
    """parse_attempt_entry returns an Attempt instance."""
    from garakboard.ingest.jsonl_parser import parse_attempt_entry

    entry = {
        "entry_type": "attempt",
        "probe": "encoding.InjectBase64",
        "prompt": "test prompt",
        "response": "test response",
        "detector_results": {"always.Fail": False},
        "run_id": "garak-run-001",
    }
    run_id = "11111111-1111-1111-1111-111111111111"
    result = parse_attempt_entry(entry, run_id)

    assert isinstance(result, Attempt)


def test_parse_attempt_entry_stores_detector_results_as_json(db_session):
    """detector_results dict stored as detector_outcome on Attempt."""
    from garakboard.ingest.jsonl_parser import parse_attempt_entry

    detector_results = {"always.Fail": False, "some.Detector": True}
    entry = {
        "entry_type": "attempt",
        "probe": "encoding.InjectBase64",
        "prompt": "test prompt",
        "response": "test response",
        "detector_results": detector_results,
        "run_id": "garak-run-001",
    }
    run_id = "11111111-1111-1111-1111-111111111111"
    result = parse_attempt_entry(entry, run_id)

    assert result.detector_outcome == detector_results


def test_parse_attempt_entry_handles_null_prompt_and_response(db_session):
    """Null prompt and response do not raise an error."""
    from garakboard.ingest.jsonl_parser import parse_attempt_entry

    entry = {
        "entry_type": "attempt",
        "probe": "encoding.InjectBase64",
        "prompt": None,
        "response": None,
        "detector_results": {},
        "run_id": "garak-run-001",
    }
    run_id = "11111111-1111-1111-1111-111111111111"
    result = parse_attempt_entry(entry, run_id)

    assert result.prompt is None
    assert result.response is None


def test_parse_attempt_entry_wrong_type_raises_value_error(db_session):
    """ValueError raised when entry_type is not 'attempt'."""
    from garakboard.ingest.jsonl_parser import parse_attempt_entry

    entry = {
        "entry_type": "eval",
        "probe": "encoding.InjectBase64",
        "prompt": "test prompt",
        "response": "test response",
        "detector_results": {"always.Fail": False},
        "run_id": "garak-run-001",
    }
    run_id = "11111111-1111-1111-1111-111111111111"

    with pytest.raises(ValueError):
        parse_attempt_entry(entry, run_id)


def test_parse_attempt_entry_empty_detector_results(db_session):
    """Empty detector_results stored as empty dict."""
    from garakboard.ingest.jsonl_parser import parse_attempt_entry

    entry = {
        "entry_type": "attempt",
        "probe": "encoding.InjectBase64",
        "prompt": "test prompt",
        "response": "test response",
        "detector_results": {},
        "run_id": "garak-run-001",
    }
    run_id = "11111111-1111-1111-1111-111111111111"
    result = parse_attempt_entry(entry, run_id)

    assert result.detector_outcome == {}


# --- ingest_jsonl_file tests ---


def test_ingest_eval_file_inserts_probe_results(db_session, tmp_path):
    """ingest_jsonl_file inserts correct number of ProbeResult rows from eval file."""
    from garakboard.ingest.jsonl_parser import ingest_jsonl_file

    jsonl_file = tmp_path / "eval.jsonl"
    jsonl_file.write_text(
        '{"entry_type": "eval", "probe": "encoding.InjectBase64", "detector": "always.Fail", "passed": 8, "failed": 2, "score": 0.8, "run_id": "garak-run-001"}\n'
        '{"entry_type": "eval", "probe": "encoding.InjectHex", "detector": "always.Fail", "passed": 10, "failed": 0, "score": 1.0, "run_id": "garak-run-001"}\n'
    )

    run_id = "11111111-1111-1111-1111-111111111111"
    result = ingest_jsonl_file(str(jsonl_file), run_id, db_session)

    assert result.probe_results_count == 2
    assert result.attempts_count == 0
    assert result.skipped_count == 0

    db_session.flush()
    inserted = db_session.query(ProbeResult).all()
    assert len(inserted) == 2


def test_ingest_attempt_file_inserts_attempts(db_session, tmp_path):
    """ingest_jsonl_file inserts correct number of Attempt rows from attempt file."""
    from garakboard.ingest.jsonl_parser import ingest_jsonl_file

    jsonl_file = tmp_path / "attempts.jsonl"
    jsonl_file.write_text(
        '{"entry_type": "attempt", "probe": "encoding.InjectBase64", "prompt": "test1", "response": "resp1", "detector_results": {}, "run_id": "garak-run-001"}\n'
        '{"entry_type": "attempt", "probe": "encoding.InjectBase64", "prompt": "test2", "response": "resp2", "detector_results": {}, "run_id": "garak-run-001"}\n'
    )

    run_id = "11111111-1111-1111-1111-111111111111"
    result = ingest_jsonl_file(str(jsonl_file), run_id, db_session)

    assert result.attempts_count == 2
    assert result.probe_results_count == 0
    assert result.skipped_count == 0

    db_session.flush()
    inserted = db_session.query(Attempt).all()
    assert len(inserted) == 2


def test_ingest_mixed_file_handles_all_types(db_session, tmp_path):
    """ingest_jsonl_file correctly processes a mixed file with eval, attempt, and unknown entries."""
    from garakboard.ingest.jsonl_parser import ingest_jsonl_file

    jsonl_file = tmp_path / "mixed.jsonl"
    jsonl_file.write_text(
        '{"entry_type": "eval", "probe": "encoding.InjectBase64", "detector": "always.Fail", "passed": 8, "failed": 2, "score": 0.8, "run_id": "garak-run-001"}\n'
        '{"entry_type": "attempt", "probe": "encoding.InjectBase64", "prompt": "test prompt", "response": "test response", "detector_results": {"always.Fail": false}, "run_id": "garak-run-001"}\n'
        '{"entry_type": "unknown_future_type", "data": "should be skipped"}\n'
        '{"entry_type": "eval", "probe": "malwaregen.Evasion", "detector": "mitigation.MitigationBypass", "passed": 3, "failed": 7, "score": 0.3, "run_id": "garak-run-001"}\n'
    )

    run_id = "11111111-1111-1111-1111-111111111111"
    result = ingest_jsonl_file(str(jsonl_file), run_id, db_session)

    assert result.probe_results_count == 2
    assert result.attempts_count == 1
    assert result.skipped_count == 1


def test_ingest_skips_unknown_entry_types(db_session, tmp_path):
    """Unknown entry_type entries are silently skipped and counted in skipped_count."""
    from garakboard.ingest.jsonl_parser import ingest_jsonl_file

    jsonl_file = tmp_path / "unknown.jsonl"
    jsonl_file.write_text(
        '{"entry_type": "unknown_type", "data": "test"}\n'
        '{"entry_type": "another_unknown", "foo": "bar"}\n'
    )

    run_id = "11111111-1111-1111-1111-111111111111"
    result = ingest_jsonl_file(str(jsonl_file), run_id, db_session)

    assert result.skipped_count == 2
    assert result.probe_results_count == 0
    assert result.attempts_count == 0


def test_ingest_returns_correct_counts(db_session, tmp_path):
    """IngestResult counts match actual rows inserted."""
    from garakboard.ingest.jsonl_parser import ingest_jsonl_file, IngestResult

    jsonl_file = tmp_path / "counts.jsonl"
    jsonl_file.write_text(
        '{"entry_type": "eval", "probe": "probe.A", "detector": "det.A", "passed": 1, "failed": 1, "score": 0.5, "run_id": "garak-run-001"}\n'
        '{"entry_type": "eval", "probe": "probe.B", "detector": "det.B", "passed": 2, "failed": 2, "score": 0.5, "run_id": "garak-run-001"}\n'
        '{"entry_type": "attempt", "probe": "probe.A", "prompt": "p1", "response": "r1", "detector_results": {}, "run_id": "garak-run-001"}\n'
        '{"entry_type": "attempt", "probe": "probe.B", "prompt": "p2", "response": "r2", "detector_results": {}, "run_id": "garak-run-001"}\n'
        '{"entry_type": "attempt", "probe": "probe.C", "prompt": "p3", "response": "r3", "detector_results": {}, "run_id": "garak-run-001"}\n'
    )

    run_id = "11111111-1111-1111-1111-111111111111"
    result = ingest_jsonl_file(str(jsonl_file), run_id, db_session)

    assert isinstance(result, IngestResult)
    assert result.probe_results_count == 2
    assert result.attempts_count == 3
    assert result.skipped_count == 0


def test_ingest_file_not_found_raises(db_session):
    """FileNotFoundError raised when file_path does not exist."""
    from garakboard.ingest.jsonl_parser import ingest_jsonl_file

    run_id = "11111111-1111-1111-1111-111111111111"

    with pytest.raises(FileNotFoundError):
        ingest_jsonl_file("/nonexistent/path/file.jsonl", run_id, db_session)


def test_ingest_handles_json_decode_error(db_session, tmp_path):
    """Lines with invalid JSON are skipped and counted as skipped."""
    from garakboard.ingest.jsonl_parser import ingest_jsonl_file

    jsonl_file = tmp_path / "bad_json.jsonl"
    jsonl_file.write_text(
        '{"entry_type": "eval", "probe": "probe.A", "detector": "det.A", "passed": 1, "failed": 1, "score": 0.5, "run_id": "garak-run-001"}\n'
        'not valid json line\n'
        '{"entry_type": "attempt", "probe": "probe.A", "prompt": "p1", "response": "r1", "detector_results": {}, "run_id": "garak-run-001"}\n'
    )

    run_id = "11111111-1111-1111-1111-111111111111"
    result = ingest_jsonl_file(str(jsonl_file), run_id, db_session)

    assert result.skipped_count == 1
    assert result.probe_results_count == 1
    assert result.attempts_count == 1
