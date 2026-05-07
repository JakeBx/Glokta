"""Unit tests for Prefect pipeline flows — TDD red-green.

Tests target the pure business logic functions extracted from the flows:
  - _process_pending_runs(db, scan_fn) — state machine
  - _execute_scan(run_id, model_name, probe_categories, db) — garak execution
  - _discover_and_queue(db, api_key, top_n, max_cost, ttl_days) — discovery

These functions have no Prefect dependency so no server is required.
"""

import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, call

import pytest

os.environ["TESTING"] = "1"

from garakboard.ingest.jsonl_parser import IngestResult
from garakboard.models import Model, Run


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _seed_model(db_session, name: str) -> Model:
    model = Model(name=name, provider="test", snapshot_date=date.today())
    db_session.add(model)
    db_session.flush()
    return model


def _seed_run(db_session, model: Model, status: str = "pending") -> Run:
    run = Run(model_id=model.id, status=status, triggered_by="test")
    db_session.add(run)
    db_session.flush()
    return run


# ---------------------------------------------------------------------------
# _process_pending_runs — state machine tests
# ---------------------------------------------------------------------------


class TestProcessPendingRuns:
    def test_transitions_pending_run_to_complete(self, db_session):
        """A pending run transitions running → complete when scan_fn succeeds."""
        from garakboard.pipeline.flows import _process_pending_runs

        model = _seed_model(db_session, "test/model-proc-1")
        run = _seed_run(db_session, model, "pending")

        mock_scan = MagicMock(return_value={"probe_results_count": 10})
        _process_pending_runs(db_session, mock_scan)

        db_session.refresh(run)
        assert run.status == "complete"
        assert run.started_at is not None
        assert run.completed_at is not None

    def test_marks_run_failed_when_scan_raises(self, db_session):
        """A pending run is marked failed when scan_fn raises an exception."""
        from garakboard.pipeline.flows import _process_pending_runs

        model = _seed_model(db_session, "test/model-proc-2")
        run = _seed_run(db_session, model, "pending")

        mock_scan = MagicMock(side_effect=RuntimeError("garak failed"))
        _process_pending_runs(db_session, mock_scan)

        db_session.refresh(run)
        assert run.status == "failed"
        assert run.completed_at is not None

    def test_noop_when_no_pending_runs(self, db_session):
        """When no pending runs exist, scan_fn is never called."""
        from garakboard.pipeline.flows import _process_pending_runs

        mock_scan = MagicMock()
        _process_pending_runs(db_session, mock_scan)

        mock_scan.assert_not_called()

    def test_does_not_pick_up_running_runs(self, db_session):
        """Runs already in status=running are ignored (SKIP LOCKED semantics)."""
        from garakboard.pipeline.flows import _process_pending_runs

        model = _seed_model(db_session, "test/model-proc-3")
        run = _seed_run(db_session, model, "running")

        mock_scan = MagicMock()
        _process_pending_runs(db_session, mock_scan)

        mock_scan.assert_not_called()
        db_session.refresh(run)
        assert run.status == "running"

    def test_completed_at_set_on_failure(self, db_session):
        """completed_at is set even when the scan fails."""
        from garakboard.pipeline.flows import _process_pending_runs

        model = _seed_model(db_session, "test/model-proc-4")
        run = _seed_run(db_session, model, "pending")

        mock_scan = MagicMock(side_effect=RuntimeError("oops"))
        _process_pending_runs(db_session, mock_scan)

        db_session.refresh(run)
        assert run.completed_at is not None

    def test_processes_multiple_pending_runs(self, db_session):
        """All pending runs are processed in a single call."""
        from garakboard.pipeline.flows import _process_pending_runs

        model = _seed_model(db_session, "test/model-proc-5")
        run1 = _seed_run(db_session, model, "pending")
        run2 = _seed_run(db_session, model, "pending")

        mock_scan = MagicMock(return_value=None)
        _process_pending_runs(db_session, mock_scan)

        assert mock_scan.call_count == 2
        db_session.refresh(run1)
        db_session.refresh(run2)
        assert run1.status == "complete"
        assert run2.status == "complete"

    def test_scan_fn_called_with_run_id_and_model_name(self, db_session):
        """scan_fn receives (run_id_str, model_name, probe_categories)."""
        from garakboard.pipeline.flows import _process_pending_runs

        model = _seed_model(db_session, "test/model-proc-6")
        run = _seed_run(db_session, model, "pending")

        mock_scan = MagicMock(return_value=None)
        _process_pending_runs(db_session, mock_scan)

        mock_scan.assert_called_once_with(str(run.id), model.name, [])


# ---------------------------------------------------------------------------
# _execute_scan — core garak execution tests
# ---------------------------------------------------------------------------


class TestExecuteScan:
    def test_calls_build_config_run_garak_and_ingest(self, db_session):
        """_execute_scan calls build_garak_config → run_garak → ingest_jsonl_file."""
        from garakboard.pipeline.flows import _execute_scan

        model = _seed_model(db_session, "test/model-scan-1")
        run = _seed_run(db_session, model, "running")

        ingest_result = IngestResult(probe_results_count=5, attempts_count=25, skipped_count=0)

        with patch("garakboard.pipeline.flows.build_garak_config", return_value={}) as mock_cfg:
            with patch("garakboard.pipeline.flows.run_garak", return_value="/tmp/out.jsonl") as mock_garak:
                with patch("garakboard.pipeline.flows.ingest_jsonl_file", return_value=ingest_result) as mock_ingest:
                    with patch("garakboard.pipeline.flows.compute_remaining_probes", return_value=["encoding.InjectBase64"]):
                        result = _execute_scan(str(run.id), model.name, [], db_session)

        mock_cfg.assert_called_once()
        mock_garak.assert_called_once()
        mock_ingest.assert_called_once()
        assert result["probe_results_count"] == 5

    def test_raises_empty_ingest_error_on_zero_results(self, db_session):
        """_execute_scan raises EmptyIngestError when garak produces no results."""
        from garakboard.pipeline.flows import _execute_scan, EmptyIngestError

        model = _seed_model(db_session, "test/model-scan-2")
        run = _seed_run(db_session, model, "running")

        empty = IngestResult(probe_results_count=0, attempts_count=0, skipped_count=0)

        with patch("garakboard.pipeline.flows.build_garak_config", return_value={}):
            with patch("garakboard.pipeline.flows.run_garak", return_value="/tmp/out.jsonl"):
                with patch("garakboard.pipeline.flows.ingest_jsonl_file", return_value=empty):
                    with patch("garakboard.pipeline.flows.compute_remaining_probes", return_value=["encoding.InjectBase64"]):
                        with pytest.raises(EmptyIngestError):
                            _execute_scan(str(run.id), model.name, [], db_session)

    def test_skips_scan_when_all_probes_done(self, db_session):
        """When compute_remaining_probes returns empty, garak is not run."""
        from garakboard.pipeline.flows import _execute_scan

        model = _seed_model(db_session, "test/model-scan-3")
        run = _seed_run(db_session, model, "running")

        with patch("garakboard.pipeline.flows.compute_remaining_probes", return_value=[]):
            with patch("garakboard.pipeline.flows.run_garak") as mock_garak:
                result = _execute_scan(str(run.id), model.name, [], db_session)

        mock_garak.assert_not_called()
        assert result.get("skipped") is True

    def test_returns_probe_and_attempt_counts(self, db_session):
        """Return dict contains probe_results_count and attempts_count."""
        from garakboard.pipeline.flows import _execute_scan

        model = _seed_model(db_session, "test/model-scan-4")
        run = _seed_run(db_session, model, "running")

        ingest_result = IngestResult(probe_results_count=12, attempts_count=60, skipped_count=0)

        with patch("garakboard.pipeline.flows.build_garak_config", return_value={}):
            with patch("garakboard.pipeline.flows.run_garak", return_value="/tmp/out.jsonl"):
                with patch("garakboard.pipeline.flows.ingest_jsonl_file", return_value=ingest_result):
                    with patch("garakboard.pipeline.flows.compute_remaining_probes", return_value=["encoding.InjectBase64"]):
                        result = _execute_scan(str(run.id), model.name, [], db_session)

        assert result["probe_results_count"] == 12
        assert result["attempts_count"] == 60
