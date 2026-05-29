"""Prefect pipeline flows for Glokta scan orchestration.

Thin Prefect adapter — all business logic lives in application/scan_service.py.
"""

import logging

from prefect import flow, task
from sqlalchemy.orm import Session

from glokta.config import settings
from glokta.infrastructure.db.session import SessionLocal
from glokta.application.scan_service import (
    EmptyIngestError,
    StaleRunError,
    execute_scan,
    process_pending_run,
    queue_stale_models,
    reap_stale_runs,
    sync_model_statuses,
)
from glokta.infrastructure.db.orm import Run
from glokta.infrastructure.hf.client import fetch_top_hf_models
from glokta.infrastructure.openrouter.client import fetch_top_models

# Backward-compat aliases — tests import these names from flows
_process_pending_runs = process_pending_run
_reap_stale_runs = reap_stale_runs
_execute_scan = execute_scan

logger = logging.getLogger(__name__)

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
    from datetime import datetime, timezone

    db = SessionLocal()
    try:
        run = db.query(Run).filter(Run.id == run_id).first()
        if run is None or run.status != "running":
            raise StaleRunError(
                f"Run {run_id} is in state '{getattr(run, 'status', 'missing')}'; skipping attempt"
            )

        result = execute_scan(
            run_id,
            model_name,
            probe_categories,
            db,
            probe_prompt_cap=probe_prompt_cap,
            parallel_attempts_override=parallel_attempts_override,
            scan_timeout_seconds=scan_timeout_seconds,
        )

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
        stale_after = (settings.garak_timeout_seconds * 4) + sum(_RETRY_DELAYS) + 600
        reap_stale_runs(db, stale_after)

        process_pending_run(
            db,
            lambda rid, mname, cats, cap, parallel, timeout: execute_garak_scan_task(
                rid, mname, cats, cap, parallel, timeout
            ),
        )
    finally:
        db.close()


@flow(name="sync-top-models", log_prints=True)
def sync_top_models() -> None:
    """Fetch top models from OpenRouter and HuggingFace and sync ACTIVE/ARCHIVED status. Scheduled weekly."""
    if not settings.scheduler_enabled:
        return
    db = SessionLocal()
    try:
        or_models = fetch_top_models(
            api_key=settings.openrouter_api_key,
            top_n=settings.scheduler_top_n_models,
            max_scan_cost_usd=settings.scheduler_max_scan_cost_usd,
        )
        hf_models = (
            fetch_top_hf_models(hf_token=settings.hf_token, top_n=settings.scheduler_hf_top_n_models)
            if settings.hf_token and settings.scheduler_hf_top_n_models > 0
            else []
        )
        result = sync_model_statuses(db, or_models, hf_models)
        logger.info("sync_top_models: upserted=%d archived=%d", result["upserted"], result["archived"])
    except Exception as exc:
        logger.error("sync_top_models failed: %s", exc)
        db.rollback()
        raise
    finally:
        db.close()


@flow(name="trigger-weekly-scans", log_prints=True)
def trigger_weekly_scans() -> None:
    """Queue pending runs for all ACTIVE models with a stale last_scan_at. Scheduled weekly."""
    if not settings.scheduler_enabled:
        return
    db = SessionLocal()
    try:
        result = queue_stale_models(db, scan_ttl_days=settings.scheduler_scan_ttl_days)
        logger.info("trigger_weekly_scans: queued=%d skipped=%d", result["queued"], result["skipped"])
    except Exception as exc:
        logger.error("trigger_weekly_scans failed: %s", exc)
        db.rollback()
        raise
    finally:
        db.close()
