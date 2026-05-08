"""Prefect pipeline flows for GarakBoard scan orchestration.

Architecture:
  _process_pending_runs(db, scan_fn) — pure state machine; testable without Prefect
  _execute_scan(run_id, model_name, probe_categories, db) — core garak logic; testable
  _discover_and_queue(db, ...) — discovery logic; testable

  execute_garak_scan_task — @task wrapping _execute_scan, with retries
  scan_pending_runs — @flow, picks pending runs and calls the task
  discover_and_queue_scans — @flow, fetches top models and creates pending runs
"""

import io
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Callable

import yaml
from prefect import flow, task
from sqlalchemy.exc import CompileError
from sqlalchemy.orm import Session

from garakboard.config import settings
from garakboard.database import SessionLocal
from garakboard.ingest.jsonl_parser import ingest_jsonl_file
from garakboard.models import Model, ProbeResult, Run
from garakboard.worker.garak_runner import (
    DEFAULT_PROBE_CATEGORIES,
    build_garak_config,
    compute_remaining_probes,
    run_garak,
)
from garakboard.worker.openrouter_client import fetch_top_models

logger = logging.getLogger(__name__)


class EmptyIngestError(Exception):
    """Raised when garak exits cleanly but produces no probe results or attempts."""


# ---------------------------------------------------------------------------
# Pure business logic — no Prefect dependency, fully testable
# ---------------------------------------------------------------------------


def _reap_stale_runs(db: Session, stale_after_seconds: int) -> int:
    """Mark runs stuck in 'running' past the scan timeout as 'failed'.

    Protects against zombie runs left behind when the worker process is killed
    mid-scan (OOM, SIGKILL, container restart) where the finally block never runs.

    Returns the count of runs reaped.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    stale = (
        db.query(Run)
        .filter(
            Run.status == "running",
            Run.started_at.isnot(None),
            Run.started_at < cutoff,
        )
        .all()
    )
    for run in stale:
        logger.warning("Reaping stale run %s (started_at=%s)", run.id, run.started_at)
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
    if stale:
        db.commit()
    return len(stale)


def _process_pending_runs(db: Session, scan_fn: Callable) -> None:
    """Claim and process exactly one pending run via scan_fn.

    Claiming one run per flow invocation closes the race condition where a looping
    approach would release the FOR UPDATE lock on un-processed rows after the first
    db.commit(), allowing a concurrent flow to pick up the same runs.

    scan_fn(run_id: str, model_name: str, probe_categories: list[str])
    Should raise on failure; return value is ignored.
    """
    # SKIP LOCKED prevents concurrent workers from picking the same run.
    # Falls back to a plain query for dialects that don't support it (SQLite in tests).
    try:
        run = (
            db.query(Run)
            .filter(Run.status == "pending")
            .with_for_update(skip_locked=True)
            .first()
        )
    except CompileError:
        db.rollback()
        run = db.query(Run).filter(Run.status == "pending").first()

    if run is None:
        return

    model = db.query(Model).filter(Model.id == run.model_id).first()
    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    db.commit()
    try:
        scan_fn(str(run.id), model.name, [])
        run.status = "complete"
    except Exception:
        run.status = "failed"
    finally:
        run.completed_at = datetime.now(timezone.utc)
        db.commit()


def _execute_scan(
    run_id: str,
    model_name: str,
    probe_categories: list[str],
    db: Session,
) -> dict:
    """Core garak execution: build config, run, ingest JSONL.

    Returns dict with probe_results_count and attempts_count.
    Raises EmptyIngestError when garak exits 0 with no output.
    """
    # Resume support: skip probes already completed in a prior attempt
    done_probes = {
        row[0]
        for row in db.query(ProbeResult.probe_name)
        .filter(ProbeResult.run_id == run_id)
        .distinct()
        .all()
    }
    remaining = compute_remaining_probes(
        done_probes,
        probe_categories if probe_categories else DEFAULT_PROBE_CATEGORIES,
    )

    if not remaining:
        logger.info(f"Run {run_id}: all probes already complete, skipping scan")
        return {"probe_results_count": len(done_probes), "skipped": True}

    with tempfile.TemporaryDirectory() as output_dir:
        config = build_garak_config(
            model_name=model_name,
            probe_categories=probe_categories,
            output_dir=output_dir,
            parallel_attempts=settings.garak_parallel_attempts,
            rpm_limit=settings.openrouter_rpm_limit,
            soft_probe_prompt_cap=settings.garak_soft_probe_prompt_cap,
            probe_spec_override=",".join(remaining),
        )

        jsonl_path = run_garak(
            config,
            settings.openrouter_api_key,
            timeout=settings.garak_timeout_seconds,
        )

        # Store run metadata while the temp dir is still alive
        run = db.query(Run).filter(Run.id == run_id).first()
        try:
            import garak as _garak

            run.garak_version = _garak.__version__
            run.garak_config = yaml.dump(config, default_flow_style=False)
            with open(jsonl_path, "r", encoding="utf-8", errors="replace") as jf:
                run.raw_output = jf.read()
            db.flush()
        except Exception as exc:
            logger.warning(f"Failed to store run metadata for {run_id}: {exc}")

        source = io.StringIO(run.raw_output) if run.raw_output else jsonl_path
        result = ingest_jsonl_file(source, run_id, db)
        db.commit()

    if result.probe_results_count == 0 and result.attempts_count == 0:
        raise EmptyIngestError(
            f"Run {run_id}: garak exited cleanly but produced zero results — "
            "possible generator or routing failure"
        )

    logger.info(
        f"Run {run_id}: {result.probe_results_count} probe results, "
        f"{result.attempts_count} attempts"
    )
    return {
        "probe_results_count": result.probe_results_count,
        "attempts_count": result.attempts_count,
    }


def _discover_and_queue(
    db: Session,
    api_key: str,
    top_n: int,
    max_scan_cost_usd: float,
    scan_ttl_days: int,
) -> dict:
    """Fetch top models from OpenRouter and create pending runs for stale ones.

    Returns dict with queued and skipped counts.
    """
    from datetime import date

    top_models = fetch_top_models(
        api_key=api_key,
        top_n=top_n,
        max_scan_cost_usd=max_scan_cost_usd,
    )

    staleness_cutoff = datetime.now(timezone.utc) - timedelta(days=scan_ttl_days)
    queued = 0
    skipped = 0

    for model_data in top_models:
        openrouter_id = model_data.get("id", "")
        if not openrouter_id:
            continue

        model_name = (
            openrouter_id
            if openrouter_id.startswith("openrouter/")
            else f"openrouter/{openrouter_id}"
        )

        model = db.query(Model).filter(Model.name == model_name).first()
        if model is None:
            model = Model(
                name=model_name,
                provider=model_name.split("/")[1] if "/" in model_name else model_name,
                snapshot_date=date.today(),
            )
            db.add(model)
            db.flush()

        # Skip if already pending or running
        active = (
            db.query(Run)
            .filter(Run.model_id == model.id, Run.status.in_(["pending", "running"]))
            .first()
        )
        if active:
            skipped += 1
            continue

        # Skip if recently completed within TTL
        latest = (
            db.query(Run)
            .filter(Run.model_id == model.id, Run.status == "complete")
            .order_by(Run.completed_at.desc())
            .first()
        )
        if latest and latest.completed_at:
            completed = latest.completed_at
            if completed.tzinfo is None:
                completed = completed.replace(tzinfo=timezone.utc)
            if completed >= staleness_cutoff:
                skipped += 1
                continue

        run = Run(model_id=model.id, triggered_by="scheduled", status="pending")
        db.add(run)
        db.commit()
        queued += 1

    logger.info(f"discover_and_queue: queued={queued}, skipped={skipped}")
    return {"queued": queued, "skipped": skipped}


# ---------------------------------------------------------------------------
# Prefect tasks and flows
# ---------------------------------------------------------------------------


@task(
    name="execute-garak-scan",
    retries=3,
    retry_delay_seconds=[30, 60, 120],
)
def execute_garak_scan_task(
    run_id: str,
    model_name: str,
    probe_categories: list[str],
) -> dict:
    """Prefect task: run a garak scan with automatic retries on failure."""
    db = SessionLocal()
    try:
        return _execute_scan(run_id, model_name, probe_categories, db)
    finally:
        db.close()


@flow(name="scan-pending-runs", log_prints=True)
def scan_pending_runs() -> None:
    """Reap zombie runs, then claim and execute one pending run. Scheduled every 15 minutes."""
    db = SessionLocal()
    try:
        stale_after = settings.garak_timeout_seconds + 1800  # scan timeout + 30 min grace
        _reap_stale_runs(db, stale_after)
        _process_pending_runs(
            db,
            lambda rid, mname, cats: execute_garak_scan_task(rid, mname, cats),
        )
    finally:
        db.close()


@flow(name="discover-and-queue-scans", log_prints=True)
def discover_and_queue_scans() -> None:
    """Fetch top-N models from OpenRouter and queue stale ones. Scheduled weekly."""
    if not settings.scheduler_enabled:
        return
    db = SessionLocal()
    try:
        _discover_and_queue(
            db,
            api_key=settings.openrouter_api_key,
            top_n=settings.scheduler_top_n_models,
            max_scan_cost_usd=settings.scheduler_max_scan_cost_usd,
            scan_ttl_days=settings.scheduler_scan_ttl_days,
        )
    except Exception as exc:
        logger.error(f"discover_and_queue_scans failed: {exc}")
        db.rollback()
        raise
    finally:
        db.close()
