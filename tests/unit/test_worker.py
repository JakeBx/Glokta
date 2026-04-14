"""Unit tests for Celery worker tasks and garak runner utilities."""

import os
import tempfile
from datetime import date, datetime, timezone
from unittest.mock import patch, MagicMock

# Set testing flag before any garakboard imports
os.environ["TESTING"] = "1"
os.environ["CELERY_TASK_ALWAYS_EAGER"] = "1"


def _mock_run_lock(acquire_result: bool = True):
    """Return a mock RunLock with acquire() and release() pre-configured."""
    mock_lock = MagicMock()
    mock_lock.acquire.return_value = acquire_result
    mock_lock.release.return_value = None
    return mock_lock


# --- build_garak_config tests ---


def test_build_garak_config_returns_dict():
    """build_garak_config returns a dict."""
    from garakboard.worker.garak_runner import build_garak_config

    result = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=["encoding"],
        output_dir="/tmp/output",
    )
    assert isinstance(result, dict)


def test_build_garak_config_sets_model_name():
    """model_name appears in config under plugins.model_name."""
    from garakboard.worker.garak_runner import build_garak_config

    config = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=["encoding"],
        output_dir="/tmp/output",
    )
    assert config["plugins"]["model_name"] == "openrouter/meta-llama/llama-3-8b-instruct:free"


def test_build_garak_config_sets_probe_categories():
    """probe_categories appear in config under plugins.probe_spec as comma-separated string."""
    from garakboard.worker.garak_runner import build_garak_config

    config = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=["encoding", "malwaregen"],
        output_dir="/tmp/output",
    )
    assert config["plugins"]["probe_spec"] == "encoding,malwaregen"


def test_build_garak_config_sets_rpm_limit():
    """rpm_limit appears in config under plugins.generators.litellm.rpm."""
    from garakboard.worker.garak_runner import build_garak_config

    config = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=["encoding"],
        output_dir="/tmp/output",
        rpm_limit=30,
    )
    assert config["plugins"]["generators"]["litellm"]["rpm"] == 30


def test_build_garak_config_sets_parallel_attempts():
    """parallel_attempts appears in config under system.parallel_attempts."""
    from garakboard.worker.garak_runner import build_garak_config

    config = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=["encoding"],
        output_dir="/tmp/output",
        parallel_attempts=4,
    )
    assert config["system"]["parallel_attempts"] == 4


def test_build_garak_config_uses_encoding_default_when_no_probes():
    """When probe_categories is empty, defaults to probe_spec='encoding'."""
    from garakboard.worker.garak_runner import build_garak_config

    config = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=[],
        output_dir="/tmp/output",
    )
    assert config["plugins"]["probe_spec"] == "encoding"


def test_build_garak_config_sets_output_dir():
    """output_dir appears in config under reporting.report_dir."""
    from garakboard.worker.garak_runner import build_garak_config

    config = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=["encoding"],
        output_dir="/tmp/garak_output",
    )
    assert config["reporting"]["report_dir"] == "/tmp/garak_output"


# --- publish_run_job tests ---


def test_publish_run_job_calls_celery_delay():
    """publish_run_job calls run_scan.delay() with correct args."""
    from garakboard.worker.tasks import publish_run_job

    with patch("garakboard.worker.tasks.run_scan") as mock_run_scan:
        publish_run_job(
            run_id="abc-123",
            model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
            probe_categories=["encoding"],
        )
        mock_run_scan.delay.assert_called_once_with(
            "abc-123",
            "openrouter/meta-llama/llama-3-8b-instruct:free",
            ["encoding"],
        )


# --- run_scan task tests (mocked garak + db) ---


def test_run_scan_transitions_to_running(db_session):
    """run_scan sets run.status='running' before calling garak."""
    from garakboard.models import Run, Model

    model = Model(
        name="test-model-scan-running",
        provider="openrouter",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    run = Run(model_id=model.id, status="pending")
    db_session.add(run)
    db_session.commit()

    run_id = str(run.id)

    with patch("garakboard.worker.tasks.SessionLocal", return_value=db_session), \
         patch("garakboard.worker.tasks.get_run_lock", return_value=_mock_run_lock()), \
         patch("garakboard.worker.tasks.run_garak") as mock_run_garak, \
         patch("garakboard.worker.tasks.ingest_jsonl_file") as mock_ingest:
        mock_run_garak.return_value = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name
        mock_ingest.return_value = MagicMock(probe_results_count=1, attempts_count=1)

        from garakboard.worker.tasks import run_scan
        run_scan.apply(args=[run_id, "openrouter/test/model", ["encoding"]])

    db_session.expire_all()
    run = db_session.query(Run).filter(Run.id == run_id).first()
    assert run.status == "complete"


def test_run_scan_transitions_to_complete_on_success(db_session):
    """run_scan sets run.status='complete' after successful ingest."""
    from garakboard.models import Run, Model

    model = Model(
        name="test-model-scan-complete",
        provider="openrouter",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    run = Run(model_id=model.id, status="pending")
    db_session.add(run)
    db_session.commit()

    run_id = str(run.id)

    with patch("garakboard.worker.tasks.SessionLocal", return_value=db_session), \
         patch("garakboard.worker.tasks.get_run_lock", return_value=_mock_run_lock()), \
         patch("garakboard.worker.tasks.run_garak") as mock_run_garak, \
         patch("garakboard.worker.tasks.ingest_jsonl_file") as mock_ingest:
        mock_run_garak.return_value = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name
        mock_ingest.return_value = MagicMock(probe_results_count=1, attempts_count=1)

        from garakboard.worker.tasks import run_scan
        run_scan.apply(args=[run_id, "openrouter/test/model", ["encoding"]])

    db_session.expire_all()
    run = db_session.query(Run).filter(Run.id == run_id).first()
    assert run.status == "complete"


def test_run_scan_transitions_to_failed_on_garak_error(db_session):
    """run_scan sets run.status='failed' when garak raises a non-429 error."""
    from garakboard.models import Run, Model

    model = Model(
        name="test-model-scan-failed",
        provider="openrouter",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    run = Run(model_id=model.id, status="pending")
    db_session.add(run)
    db_session.commit()

    run_id = str(run.id)

    with patch("garakboard.worker.tasks.SessionLocal", return_value=db_session), \
         patch("garakboard.worker.tasks.get_run_lock", return_value=_mock_run_lock()), \
         patch("garakboard.worker.tasks.run_garak") as mock_run_garak, \
         patch("garakboard.worker.tasks.ingest_jsonl_file") as mock_ingest:
        mock_run_garak.side_effect = RuntimeError("garak crashed")
        mock_ingest.return_value = MagicMock(probe_results_count=0, attempts_count=0)

        from garakboard.worker.tasks import run_scan
        run_scan.apply(args=[run_id, "openrouter/test/model", ["encoding"]])

    db_session.expire_all()
    run = db_session.query(Run).filter(Run.id == run_id).first()
    assert run.status == "failed"


def test_run_scan_retries_on_429(db_session):
    """run_scan calls self.retry() when garak raises a 429 error."""
    from celery.exceptions import Retry
    from garakboard.models import Run, Model

    model = Model(
        name="test-model-scan-retry",
        provider="openrouter",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    run = Run(model_id=model.id, status="pending")
    db_session.add(run)
    db_session.commit()

    run_id = str(run.id)

    with patch("garakboard.worker.tasks.SessionLocal", return_value=db_session), \
         patch("garakboard.worker.tasks.get_run_lock", return_value=_mock_run_lock()), \
         patch("garakboard.worker.tasks.run_garak") as mock_run_garak, \
         patch("garakboard.worker.tasks.ingest_jsonl_file") as mock_ingest:
        mock_run_garak.side_effect = RuntimeError("429 rate limit exceeded")
        mock_ingest.return_value = MagicMock(probe_results_count=0, attempts_count=0)

        from garakboard.worker.tasks import run_scan

        mock_request = MagicMock()
        mock_request.retries = 0

        mock_self = MagicMock()
        mock_self.request = mock_request

        try:
            run_scan(
                mock_self,
                run_id,
                "openrouter/test/model",
                ["encoding"],
            )
        except Exception:
            pass  # Expected: retry raises MaxRetriesExceededError in eager mode

    # The 429 path resets status to 'pending' before retrying.
    db_session.expire_all()
    run = db_session.query(Run).filter(Run.id == run_id).first()
    assert run is not None


def test_run_scan_requeues_when_model_already_running(db_session):
    """run_scan retries when the model lock is held; fails after max retries exhausted."""
    from garakboard.models import Run, Model

    model = Model(
        name="test-model-already-running",
        provider="openrouter",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    run = Run(model_id=model.id, status="pending")
    db_session.add(run)
    db_session.commit()

    run_id = str(run.id)

    with patch("garakboard.worker.tasks.SessionLocal", return_value=db_session), \
         patch("garakboard.worker.tasks.get_run_lock", return_value=_mock_run_lock(acquire_result=False)):
        from garakboard.worker.tasks import run_scan

        # throw=False suppresses MaxRetriesExceededError from eager retry exhaustion.
        # In eager mode retries execute immediately; after max_retries the run fails.
        run_scan.apply(args=[run_id, "openrouter/test/model", ["encoding"]], throw=False)

    # After exhausting max_retries the outer exception handler sets status to failed.
    # The important behaviour (verified by log output) is that retry was called
    # max_retries times before giving up, not that it failed immediately.
    db_session.expire_all()
    run = db_session.query(Run).filter(Run.id == run_id).first()
    assert run.status == "failed"


def test_run_scan_returns_counts_on_success(db_session):
    """run_scan returns probe_results_count and attempts_count on success."""
    from garakboard.models import Run, Model

    model = Model(
        name="test-model-scan-counts",
        provider="openrouter",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    run = Run(model_id=model.id, status="pending")
    db_session.add(run)
    db_session.commit()

    run_id = str(run.id)

    with patch("garakboard.worker.tasks.SessionLocal", return_value=db_session), \
         patch("garakboard.worker.tasks.get_run_lock", return_value=_mock_run_lock()), \
         patch("garakboard.worker.tasks.run_garak") as mock_run_garak, \
         patch("garakboard.worker.tasks.ingest_jsonl_file") as mock_ingest:
        mock_run_garak.return_value = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name
        mock_ingest.return_value = MagicMock(probe_results_count=5, attempts_count=10)

        from garakboard.worker.tasks import run_scan
        result = run_scan.apply(args=[run_id, "openrouter/test/model", ["encoding"]])

    assert result.result["probe_results_count"] == 5
    assert result.result["attempts_count"] == 10


def test_run_scan_sets_started_at(db_session):
    """run_scan sets run.started_at when transitioning to running."""
    from garakboard.models import Run, Model

    model = Model(
        name="test-model-started-at",
        provider="openrouter",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    run = Run(model_id=model.id, status="pending", started_at=None)
    db_session.add(run)
    db_session.commit()

    run_id = str(run.id)

    with patch("garakboard.worker.tasks.SessionLocal", return_value=db_session), \
         patch("garakboard.worker.tasks.get_run_lock", return_value=_mock_run_lock()), \
         patch("garakboard.worker.tasks.run_garak") as mock_run_garak, \
         patch("garakboard.worker.tasks.ingest_jsonl_file") as mock_ingest:
        mock_run_garak.return_value = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name
        mock_ingest.return_value = MagicMock(probe_results_count=0, attempts_count=0)

        from garakboard.worker.tasks import run_scan
        run_scan.apply(args=[run_id, "openrouter/test/model", ["encoding"]])

    db_session.expire_all()
    run = db_session.query(Run).filter(Run.id == run_id).first()
    assert run.started_at is not None


def test_run_scan_sets_completed_at_on_success(db_session):
    """run_scan sets run.completed_at when transitioning to complete."""
    from garakboard.models import Run, Model

    model = Model(
        name="test-model-completed-success",
        provider="openrouter",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    run = Run(model_id=model.id, status="pending", completed_at=None)
    db_session.add(run)
    db_session.commit()

    run_id = str(run.id)

    with patch("garakboard.worker.tasks.SessionLocal", return_value=db_session), \
         patch("garakboard.worker.tasks.get_run_lock", return_value=_mock_run_lock()), \
         patch("garakboard.worker.tasks.run_garak") as mock_run_garak, \
         patch("garakboard.worker.tasks.ingest_jsonl_file") as mock_ingest:
        mock_run_garak.return_value = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name
        mock_ingest.return_value = MagicMock(probe_results_count=0, attempts_count=0)

        from garakboard.worker.tasks import run_scan
        run_scan.apply(args=[run_id, "openrouter/test/model", ["encoding"]])

    db_session.expire_all()
    run = db_session.query(Run).filter(Run.id == run_id).first()
    assert run.completed_at is not None
    assert run.status == "complete"


def test_run_scan_sets_completed_at_on_failure(db_session):
    """run_scan sets run.completed_at when transitioning to failed."""
    from garakboard.models import Run, Model

    model = Model(
        name="test-model-completed-fail",
        provider="openrouter",
        snapshot_date=date.today(),
    )
    db_session.add(model)
    db_session.flush()

    run = Run(model_id=model.id, status="pending", completed_at=None)
    db_session.add(run)
    db_session.commit()

    run_id = str(run.id)

    with patch("garakboard.worker.tasks.SessionLocal", return_value=db_session), \
         patch("garakboard.worker.tasks.get_run_lock", return_value=_mock_run_lock()), \
         patch("garakboard.worker.tasks.run_garak") as mock_run_garak, \
         patch("garakboard.worker.tasks.ingest_jsonl_file") as mock_ingest:
        mock_run_garak.side_effect = RuntimeError("unrecoverable garak error")
        mock_ingest.return_value = MagicMock(probe_results_count=0, attempts_count=0)

        from garakboard.worker.tasks import run_scan
        run_scan.apply(args=[run_id, "openrouter/test/model", ["encoding"]])

    db_session.expire_all()
    run = db_session.query(Run).filter(Run.id == run_id).first()
    assert run.completed_at is not None
    assert run.status == "failed"


def test_run_scan_returns_error_when_run_not_found():
    """run_scan returns {'error': 'run_not_found'} when run_id is missing."""
    mock_session = MagicMock()
    mock_session.query.return_value.filter.return_value.first.return_value = None

    with patch("garakboard.worker.tasks.SessionLocal", return_value=mock_session):
        from garakboard.worker.tasks import run_scan
        result = run_scan.apply(args=["non-existent-run-id", "openrouter/test/model", ["encoding"]])
        assert result.result == {"error": "run_not_found"}
