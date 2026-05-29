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
    discover_and_queue,
    execute_scan,
    process_pending_run,
    queue_coverage_remediation,
    reap_stale_runs,
)
from glokta.infrastructure.db.orm import Run
from glokta.domain.risks import ACTIVE_RISKS

# Backward-compat aliases — tests import these names from flows
_process_pending_runs = process_pending_run
_reap_stale_runs = reap_stale_runs
_execute_scan = execute_scan
_discover_and_queue = discover_and_queue
_queue_coverage_remediation = queue_coverage_remediation

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


@flow(name="discover-and-queue-scans", log_prints=True)
def discover_and_queue_scans() -> None:
    """Fetch top-N models from OpenRouter and HuggingFace and queue stale ones. Scheduled weekly."""
    if not settings.scheduler_enabled:
        return
    db = SessionLocal()
    try:
        discover_and_queue(
            db,
            api_key=settings.openrouter_api_key,
            top_n=settings.scheduler_top_n_models,
            max_scan_cost_usd=settings.scheduler_max_scan_cost_usd,
            scan_ttl_days=settings.scheduler_scan_ttl_days,
            hf_token=settings.hf_token,
            hf_top_n=settings.scheduler_hf_top_n_models,
        )
    except Exception as exc:
        logger.error(f"discover_and_queue_scans failed: {exc}")
        db.rollback()
        raise
    finally:
        db.close()


def _log_remediation_summary(result: dict) -> None:
    """Emit a structured summary of a remediation run to the logger."""
    queued = result["queued"]
    skipped_active = result["skipped_already_active"]
    skipped_complete = result["skipped_full_coverage"]
    errors = result["errors"]

    sep = "=" * 72

    logger.info(sep)
    logger.info("COVERAGE-REMEDIATION SUMMARY")
    logger.info(sep)
    logger.info("Models queued for remediation : %d", len(queued))
    logger.info("Models with full coverage (no action needed) : %d", len(skipped_complete))
    logger.info("Models skipped (active run already in flight) : %d", len(skipped_active))
    logger.info("Errors encountered             : %d", len(errors))
    logger.info(sep)

    if queued:
        logger.info("REMEDIATION QUEUED — the following models will be re-scanned:")
        for entry in queued:
            logger.info(
                "  [QUEUED] %-60s  run=%-36s  missing=%s",
                entry["model"], entry["run_id"], entry["missing_categories"],
            )
    else:
        logger.info(
            "NO MODELS QUEUED — either all models have full coverage, "
            "all gaps have active runs in flight, or no models have been scanned yet."
        )

    if skipped_active:
        logger.info("SKIPPED (active run in flight — will self-heal):")
        for name in skipped_active:
            logger.info("  [SKIP-ACTIVE] %s", name)

    if errors:
        logger.warning("ERRORS — these models were NOT remediated:")
        for msg in errors:
            logger.warning("  [ERROR] %s", msg)

    logger.info(sep)

    if queued and not errors:
        logger.info(
            "OUTCOME: REMEDIATION RUNS QUEUED SUCCESSFULLY. "
            "Coverage gaps will close once scan-pending-runs processes these runs."
        )
    elif queued and errors:
        logger.warning(
            "OUTCOME: PARTIAL — %d runs queued, but %d models had errors and were NOT remediated.",
            len(queued), len(errors),
        )
    elif not queued and not errors:
        logger.info(
            "OUTCOME: NO ACTION NEEDED — all scanned models have full risk coverage "
            "or have active runs that will close the gaps."
        )
    else:
        logger.error(
            "OUTCOME: FAILED — no runs were queued and %d errors occurred. "
            "Coverage gaps were NOT remediated. Investigate errors above.",
            len(errors),
        )

    logger.info(sep)


@flow(name="remediate-coverage-gaps", log_prints=True)
def remediate_coverage_gaps() -> None:
    """Weekly flow: find models with incomplete risk coverage and queue targeted re-scans."""
    if not settings.scheduler_enabled:
        logger.info("COVERAGE-REMEDIATION | scheduler_enabled=False — skipping")
        return

    logger.info("COVERAGE-REMEDIATION | starting | active_risks=%s", ACTIVE_RISKS)

    db = SessionLocal()
    try:
        result = queue_coverage_remediation(db)
    except Exception as exc:
        logger.error("COVERAGE-REMEDIATION | fatal error during queue phase: %s", exc)
        db.rollback()
        raise
    finally:
        db.close()

    _log_remediation_summary(result)

    if result["errors"]:
        raise RuntimeError(
            f"Coverage remediation completed with {len(result['errors'])} error(s). "
            "See logs above for details."
        )
