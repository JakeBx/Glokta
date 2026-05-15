"""Prefect pipeline flows for Glokta scan orchestration.

Architecture:
  _process_pending_runs(db, scan_fn) — pure state machine; testable without Prefect
  _execute_scan(run_id, model_name, probe_categories, db) — core garak logic; testable
  _discover_and_queue(db, ...) — discovery logic; testable

  execute_garak_scan_task — @task wrapping _execute_scan, with retries
  scan_pending_runs — @flow, picks pending runs and calls the task
  discover_and_queue_scans — @flow, fetches top models and creates pending runs
"""

import io
import json
import logging
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Callable

import yaml
from prefect import flow, task
from sqlalchemy.exc import CompileError
from sqlalchemy.orm import Session

from glokta.config import settings
from glokta.database import SessionLocal
from glokta.ingest.jsonl_parser import ingest_jsonl_file
from glokta.models import Model, ProbeResult, Run
from glokta.worker.garak_runner import (
    DEFAULT_PROBE_CATEGORIES,
    build_garak_config,
    compute_remaining_probes,
    run_garak,
)
from glokta.worker.openrouter_client import fetch_top_models

logger = logging.getLogger(__name__)


class EmptyIngestError(Exception):
    """Raised when garak exits cleanly but produces no probe results or attempts."""


class StaleRunError(Exception):
    """Raised when a run is no longer in 'running' state at retry time."""


# ---------------------------------------------------------------------------
# Pure business logic — no Prefect dependency, fully testable
# ---------------------------------------------------------------------------


def _process_pending_runs(db: Session, scan_fn: Callable) -> None:
    """Process one pending run via scan_fn. Call repeatedly to drain the queue.

    scan_fn(run_id, model_name, probe_categories, probe_prompt_cap,
            parallel_attempts_override, scan_timeout_seconds)
    Should raise on failure (after all Prefect retries exhausted).
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
    except Exception:
        db.rollback()
        run = db.query(Run).filter(Run.status == "pending").first()

    if run is None:
        return
    model = db.query(Model).filter(Model.id == run.model_id).first()
    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    db.commit()

    probe_categories = json.loads(run.probe_categories_json) if run.probe_categories_json else []

    try:
        scan_fn(
            str(run.id),
            model.name,
            probe_categories,
            run.probe_prompt_cap,
            run.parallel_attempts_override,
            run.scan_timeout_seconds,
        )
        run.status = "complete"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        # Re-fetch to see the current DB state — the task may have written "complete"
        # on a successful retry before the exception propagated here (defensive guard).
        run = db.query(Run).filter(Run.id == run.id).first()
        if run.status != "complete":
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()


def _reap_stale_runs(db: Session, stale_after_seconds: int) -> int:
    """Mark 'running' runs as 'failed' when started_at is older than stale_after_seconds.

    Returns the number of runs reaped. Runs with no started_at are never reaped.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    stale = (
        db.query(Run)
        .filter(Run.status == "running", Run.started_at <= cutoff)
        .all()
    )
    for run in stale:
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
    if stale:
        db.commit()
    return len(stale)


def _execute_scan(
    run_id: str,
    model_name: str,
    probe_categories: list[str],
    db: Session,
    probe_prompt_cap: int | None = None,
    parallel_attempts_override: int | None = None,
    scan_timeout_seconds: int | None = None,
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
            parallel_attempts=parallel_attempts_override or settings.garak_parallel_attempts,
            rpm_limit=settings.openrouter_rpm_limit,
            soft_probe_prompt_cap=probe_prompt_cap or settings.garak_soft_probe_prompt_cap,
            probe_spec_override=",".join(remaining),
        )

        jsonl_path = run_garak(
            config,
            settings.openrouter_api_key,
            timeout=scan_timeout_seconds or settings.garak_timeout_seconds,
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

_NO_RETRY_EXCEPTIONS = (EmptyIngestError, StaleRunError)
_RETRY_DELAYS = [30, 60, 120]


def _should_retry(task, task_run, state) -> bool:
    exc = state.result(raise_on_failure=False)
    return not isinstance(exc, _NO_RETRY_EXCEPTIONS)


@task(
    name="execute-garak-scan",
    retries=3,
    retry_delay_seconds=_RETRY_DELAYS,
    timeout_seconds=settings.garak_timeout_seconds + 300,
    retry_condition_fn=_should_retry,
)
def execute_garak_scan_task(
    run_id: str,
    model_name: str,
    probe_categories: list[str],
    probe_prompt_cap: int | None = None,
    parallel_attempts_override: int | None = None,
    scan_timeout_seconds: int | None = None,
) -> dict:
    """Prefect task: run a garak scan with automatic retries on failure."""
    db = SessionLocal()
    try:
        # Stale-run guard: abort without retrying if a prior except block already
        # advanced the run beyond "running" (StaleRunError is in _NO_RETRY_EXCEPTIONS).
        run = db.query(Run).filter(Run.id == run_id).first()
        if run is None or run.status != "running":
            raise StaleRunError(
                f"Run {run_id} is in state '{getattr(run, 'status', 'missing')}'; skipping attempt"
            )

        result = _execute_scan(
            run_id,
            model_name,
            probe_categories,
            db,
            probe_prompt_cap=probe_prompt_cap,
            parallel_attempts_override=parallel_attempts_override,
            scan_timeout_seconds=scan_timeout_seconds,
        )

        # Write "complete" here so the DB reflects success even if the exception
        # propagates to _process_pending_runs between retry attempts.
        if not result.get("skipped"):
            db.refresh(run)
            run.status = "complete"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()

        return result
    finally:
        db.close()


@flow(name="scan-pending-runs", log_prints=True)
def scan_pending_runs() -> None:
    """Pick one pending run and execute it. Scheduled every 2 minutes."""
    db = SessionLocal()
    try:
        # Grace period covers 4 attempts × garak timeout + sum of retry delays + buffer.
        stale_after = (settings.garak_timeout_seconds * 4) + sum(_RETRY_DELAYS) + 600
        _reap_stale_runs(db, stale_after)

        _process_pending_runs(
            db,
            lambda rid, mname, cats, cap, parallel, timeout: execute_garak_scan_task(
                rid, mname, cats, cap, parallel, timeout
            ),
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
