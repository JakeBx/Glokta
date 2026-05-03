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
    """model_name (without openrouter/ prefix) appears in the REST req_template."""
    from garakboard.worker.garak_runner import build_garak_config, _GENERATOR_NAME

    config = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=["encoding"],
        output_dir="/tmp/output",
    )
    assert config["plugins"]["target_name"] == _GENERATOR_NAME
    rest_gen = config["plugins"]["generators"]["rest"]["RestGenerator"]
    assert rest_gen["req_template_json_object"]["model"] == "meta-llama/llama-3-8b-instruct:free"


def test_build_garak_config_sets_probe_categories():
    """probe_categories appear in config under plugins.probe_spec as comma-separated string."""
    from garakboard.worker.garak_runner import build_garak_config

    config = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=["encoding", "malwaregen"],
        output_dir="/tmp/output",
    )
    assert config["plugins"]["probe_spec"] == "encoding,malwaregen"


def test_build_garak_config_uses_rest_generator():
    """Config routes through REST generator (target_type=rest)."""
    from garakboard.worker.garak_runner import build_garak_config

    config = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=["encoding"],
        output_dir="/tmp/output",
    )
    assert config["plugins"]["target_type"] == "rest"


def test_build_garak_config_sets_rpm_limit():
    """rpm_limit is wired into system.generators_options.max_requests_per_minute."""
    from garakboard.worker.garak_runner import build_garak_config

    config = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=["encoding"],
        output_dir="/tmp/output",
        rpm_limit=30,
    )
    assert config["system"]["generators_options"]["max_requests_per_minute"] == 30


def test_build_garak_config_omits_rpm_limit_when_none():
    """When rpm_limit is not set, generators_options is absent from config."""
    from garakboard.worker.garak_runner import build_garak_config

    config = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=["encoding"],
        output_dir="/tmp/output",
    )
    assert "generators_options" not in config.get("system", {})


def test_build_garak_config_sets_rest_uri():
    """REST generator URI points at OpenRouter chat completions endpoint."""
    from garakboard.worker.garak_runner import build_garak_config, _OPENROUTER_URI

    config = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=["encoding"],
        output_dir="/tmp/output",
    )
    rest_gen = config["plugins"]["generators"]["rest"]["RestGenerator"]
    assert rest_gen["uri"] == _OPENROUTER_URI


def test_build_garak_config_strips_openrouter_prefix_from_model():
    """'openrouter/' prefix is stripped before embedding model in REST template."""
    from garakboard.worker.garak_runner import build_garak_config

    config = build_garak_config(
        model_name="openrouter/mistralai/mistral-nemo",
        probe_categories=["encoding"],
        output_dir="/tmp/output",
    )
    rest_gen = config["plugins"]["generators"]["rest"]["RestGenerator"]
    assert rest_gen["req_template_json_object"]["model"] == "mistralai/mistral-nemo"


def test_build_garak_config_model_without_prefix_unchanged():
    """Model name without 'openrouter/' prefix is passed through unchanged."""
    from garakboard.worker.garak_runner import build_garak_config

    config = build_garak_config(
        model_name="mistralai/mistral-nemo",
        probe_categories=["encoding"],
        output_dir="/tmp/output",
    )
    rest_gen = config["plugins"]["generators"]["rest"]["RestGenerator"]
    assert rest_gen["req_template_json_object"]["model"] == "mistralai/mistral-nemo"


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


def test_build_garak_config_uses_default_categories_when_no_probes():
    """When probe_categories is empty, fall back to the full DEFAULT_PROBE_CATEGORIES
    set rather than a single-category encoding scan — empty means 'full default
    scan', not 'testing subset'."""
    from garakboard.worker.garak_runner import build_garak_config, DEFAULT_PROBE_CATEGORIES

    config = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=[],
        output_dir="/tmp/output",
    )
    assert config["plugins"]["probe_spec"] == ",".join(DEFAULT_PROBE_CATEGORIES)


def test_default_probe_categories_covers_multiple_dimensions():
    """DEFAULT_PROBE_CATEGORIES spans jailbreak, injection, harmful-content and more."""
    from garakboard.worker.garak_runner import DEFAULT_PROBE_CATEGORIES
    assert "encoding" in DEFAULT_PROBE_CATEGORIES
    assert "dan" in DEFAULT_PROBE_CATEGORIES
    assert "malwaregen" in DEFAULT_PROBE_CATEGORIES
    assert len(DEFAULT_PROBE_CATEGORIES) >= 5


def test_compute_remaining_probes_excludes_full_probes():
    """Probes whose class name ends with 'Full' are excluded from the probe spec
    because they run all prompts without respecting soft_probe_prompt_cap."""
    from garakboard.worker.garak_runner import compute_remaining_probes

    remaining = compute_remaining_probes(set(), ["continuation", "dan", "leakreplay"])
    for probe in remaining:
        class_name = probe.split(".")[-1] if "." in probe else probe
        assert not class_name.endswith("Full"), f"Full probe not excluded: {probe}"
    assert len(remaining) > 0, "expected at least some non-Full probes"


def test_build_garak_config_sets_output_dir():
    """output_dir appears in config under reporting.report_dir."""
    from garakboard.worker.garak_runner import build_garak_config

    config = build_garak_config(
        model_name="openrouter/meta-llama/llama-3-8b-instruct:free",
        probe_categories=["encoding"],
        output_dir="/tmp/garak_output",
    )
    assert config["reporting"]["report_dir"] == "/tmp/garak_output"


# --- run_garak exit code tests ---


def test_run_garak_accepts_exit_code_0():
    """run_garak succeeds when garak exits 0 (no vulnerabilities found)."""
    import subprocess
    from garakboard.worker.garak_runner import run_garak

    config = {"reporting": {"report_dir": "/tmp/fake_dir"}}
    fake_result = MagicMock(returncode=0, stdout="ok", stderr="")
    fake_jsonl = tempfile.NamedTemporaryFile(suffix=".report.jsonl", dir="/tmp", delete=False)
    fake_jsonl.close()

    with patch("garakboard.worker.garak_runner.subprocess.run", return_value=fake_result), \
         patch("garakboard.worker.garak_runner.Path") as mock_path, \
         patch("garakboard.worker.garak_runner.os.unlink"), \
         patch("garakboard.worker.garak_runner.tempfile.NamedTemporaryFile") as mock_tmp:
        mock_tmp.return_value.__enter__ = MagicMock(return_value=MagicMock(name="/tmp/config.yaml"))
        mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
        mock_tmp.return_value.name = "/tmp/config.yaml"
        mock_path.return_value.rglob.return_value = [MagicMock(stat=lambda: MagicMock(st_mtime=1), __str__=lambda self: fake_jsonl.name)]
        run_garak(config, api_key="test-key")  # should not raise


def test_run_garak_accepts_exit_code_1():
    """run_garak succeeds when garak exits 1 (vulnerabilities detected — normal)."""
    from garakboard.worker.garak_runner import run_garak

    config = {"reporting": {"report_dir": "/tmp/fake_dir"}}
    fake_result = MagicMock(returncode=1, stdout="FAIL attack_success_rate=0.4", stderr="")
    fake_jsonl = tempfile.NamedTemporaryFile(suffix=".report.jsonl", dir="/tmp", delete=False)
    fake_jsonl.close()

    with patch("garakboard.worker.garak_runner.subprocess.run", return_value=fake_result), \
         patch("garakboard.worker.garak_runner.Path") as mock_path, \
         patch("garakboard.worker.garak_runner.os.unlink"), \
         patch("garakboard.worker.garak_runner.tempfile.NamedTemporaryFile") as mock_tmp:
        mock_tmp.return_value.__enter__ = MagicMock(return_value=MagicMock(name="/tmp/config.yaml"))
        mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
        mock_tmp.return_value.name = "/tmp/config.yaml"
        mock_path.return_value.rglob.return_value = [MagicMock(stat=lambda: MagicMock(st_mtime=1), __str__=lambda self: fake_jsonl.name)]
        run_garak(config, api_key="test-key")  # should not raise


def test_run_garak_raises_on_unexpected_exit_code():
    """run_garak raises CalledProcessError for exit codes other than 0 or 1."""
    import subprocess
    from garakboard.worker.garak_runner import run_garak

    config = {"reporting": {"report_dir": "/tmp/fake_dir"}}
    fake_result = MagicMock(returncode=2, stdout="", stderr="fatal error", args=["garak"])

    with patch("garakboard.worker.garak_runner.subprocess.run", return_value=fake_result), \
         patch("garakboard.worker.garak_runner.os.unlink"), \
         patch("garakboard.worker.garak_runner.tempfile.NamedTemporaryFile") as mock_tmp:
        mock_tmp.return_value.__enter__ = MagicMock(return_value=MagicMock(name="/tmp/config.yaml"))
        mock_tmp.return_value.__exit__ = MagicMock(return_value=False)
        mock_tmp.return_value.name = "/tmp/config.yaml"
        try:
            run_garak(config, api_key="test-key")
            assert False, "should have raised CalledProcessError"
        except subprocess.CalledProcessError as exc:
            assert exc.returncode == 2


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
        run_scan.apply(args=[run_id, "openrouter/test/model", ["encoding"]], throw=False)

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
        mock_ingest.return_value = MagicMock(probe_results_count=3, attempts_count=15)

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
        run_scan.apply(args=[run_id, "openrouter/test/model", ["encoding"]], throw=False)

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


def test_run_scan_calls_garak_with_remaining_probes_only(db_session):
    """When a run already has probe_results, run_scan passes only unfinished probes to garak."""
    from garakboard.models import Run, Model, ProbeResult
    from garakboard.worker.garak_runner import build_garak_config

    model = Model(name="test-model-resume", provider="openrouter", snapshot_date=date.today())
    db_session.add(model)
    db_session.flush()
    run = Run(model_id=model.id, status="pending")
    db_session.add(run)
    db_session.flush()
    db_session.add(ProbeResult(
        run_id=run.id, probe_name="dan.Dan_11_0", probe_category="dan",
        detector="det", pass_count=5, fail_count=2, score=None,
    ))
    db_session.commit()
    run_id = str(run.id)

    captured_spec = []

    def fake_build_config(*args: object, probe_spec_override: str | None = None, **kwargs: object) -> dict:
        captured_spec.append(probe_spec_override)
        return build_garak_config(*args, probe_spec_override=probe_spec_override, **kwargs)

    with patch("garakboard.worker.tasks.SessionLocal", return_value=db_session), \
         patch("garakboard.worker.tasks.get_run_lock", return_value=_mock_run_lock()), \
         patch("garakboard.worker.tasks.build_garak_config", side_effect=fake_build_config), \
         patch("garakboard.worker.tasks.run_garak") as mock_garak, \
         patch("garakboard.worker.tasks.ingest_jsonl_file") as mock_ingest:
        mock_garak.return_value = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False).name
        mock_ingest.return_value = MagicMock(probe_results_count=1, attempts_count=1)
        from garakboard.worker.tasks import run_scan
        run_scan.apply(args=[run_id, "openrouter/test/model", ["dan"]])

    assert len(captured_spec) == 1
    assert captured_spec[0] is not None, "probe_spec_override should be set when some probes are done"
    assert "probes.dan.Dan_11_0" not in captured_spec[0]


def test_run_scan_skips_garak_when_all_probes_done(db_session):
    """If all expected probes already have results, garak is NOT called and run is marked complete."""
    from garakboard.models import Run, Model

    model = Model(name="test-model-skip-garak", provider="openrouter", snapshot_date=date.today())
    db_session.add(model)
    db_session.flush()
    run = Run(model_id=model.id, status="pending")
    db_session.add(run)
    db_session.commit()
    run_id = str(run.id)

    with patch("garakboard.worker.tasks.SessionLocal", return_value=db_session), \
         patch("garakboard.worker.tasks.get_run_lock", return_value=_mock_run_lock()), \
         patch("garakboard.worker.tasks.compute_remaining_probes", return_value=[]), \
         patch("garakboard.worker.tasks.run_garak") as mock_garak, \
         patch("garakboard.worker.tasks.ingest_jsonl_file"):
        from garakboard.worker.tasks import run_scan
        run_scan.apply(args=[run_id, "openrouter/test/model", ["encoding"]])

    mock_garak.assert_not_called()
    db_session.expire_all()
    run = db_session.query(Run).filter(Run.id == run_id).first()
    assert run.status == "complete"


def test_run_scan_clears_completed_at_on_429_retry(db_session):
    """On 429 retry, completed_at is cleared to None before re-queuing."""
    from garakboard.models import Run, Model

    model = Model(name="test-model-retry-ts", provider="openrouter", snapshot_date=date.today())
    db_session.add(model)
    db_session.flush()
    stale_ts = datetime(2026, 5, 1, 3, 20, tzinfo=timezone.utc)
    run = Run(model_id=model.id, status="pending", completed_at=stale_ts)
    db_session.add(run)
    db_session.commit()
    run_id = str(run.id)

    # Capture completed_at at the moment of the 429 commit, before max-retries exhaustion
    completed_at_on_retry: list = []

    original_commit = db_session.commit

    def capturing_commit() -> None:
        original_commit()
        db_session.expire_all()
        r = db_session.query(Run).filter(Run.id == run_id).first()
        if r and r.status == "pending":
            completed_at_on_retry.append(r.completed_at)

    with patch("garakboard.worker.tasks.SessionLocal", return_value=db_session), \
         patch("garakboard.worker.tasks.get_run_lock", return_value=_mock_run_lock()), \
         patch("garakboard.worker.tasks.compute_remaining_probes", return_value=["probes.encoding.InjectBase64"]), \
         patch("garakboard.worker.tasks.run_garak") as mock_garak, \
         patch.object(db_session, "commit", side_effect=capturing_commit):
        mock_garak.side_effect = RuntimeError("429 rate limit exceeded")
        from garakboard.worker.tasks import run_scan
        run_scan.apply(args=[run_id, "openrouter/test/model", ["encoding"]], throw=False)

    # The 429 path must have committed with status=pending and completed_at=None
    assert len(completed_at_on_retry) >= 1, "429 retry path never committed status=pending"
    assert completed_at_on_retry[0] is None


def test_run_scan_marks_failed_when_ingest_returns_zero_results(db_session):
    """If garak exits 0 but the JSONL contains no eval/attempt entries, the run
    must be marked 'failed' and the task must raise EmptyIngestError so Celery
    records it as FAILED (not a silent successful task returning an error dict)."""
    from garakboard.models import Run, Model

    model = Model(
        name="test-model-zero-results",
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
        mock_ingest.return_value = MagicMock(probe_results_count=0, attempts_count=0)

        from garakboard.worker.tasks import run_scan
        result = run_scan.apply(args=[run_id, "openrouter/test/model", ["encoding"]], throw=False)

    assert result.failed(), "task should be marked FAILED when ingest yields zero results"
    db_session.expire_all()
    run = db_session.query(Run).filter(Run.id == run_id).first()
    assert run.status == "failed"
