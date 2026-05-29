"""Pure scan orchestration logic — no Prefect dependency."""

import io
import json
import logging
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import yaml
from sqlalchemy import or_ as sa_or_
from sqlalchemy.orm import Session

from glokta.config import settings
from glokta.application.ingest import ingest_jsonl_file
from glokta.infrastructure.db.orm import Model, Run
from glokta.infrastructure.db.repos import ModelRepository, ProbeResultRepository, RunRepository, ScanDlqRepository
from glokta.infrastructure.garak.runner import (
    DEFAULT_PROBE_CATEGORIES,
    build_garak_config,
    compute_remaining_probes,
    run_garak,
)
from glokta.infrastructure.hf.client import fetch_top_hf_models
from glokta.infrastructure.openrouter.client import fetch_top_models

logger = logging.getLogger(__name__)


class EmptyIngestError(Exception):
    """Raised when garak exits cleanly but produces no probe results or attempts."""


class StaleRunError(Exception):
    """Raised when a run is no longer in 'running' state at retry time."""


def process_pending_run(db: Session, scan_fn: Callable) -> None:
    """Process one pending run via scan_fn. Call repeatedly to drain the queue.

    scan_fn(run_id, model_name, probe_categories, probe_prompt_cap,
            parallel_attempts_override, scan_timeout_seconds)
    Should raise on failure (after all retries exhausted).
    """
    run_repo = RunRepository(db)
    run = run_repo.pending_one_locked()

    if run is None:
        return
    model = ModelRepository(db).find_by_id(run.model_id)
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
    except Exception as exc:
        # Re-fetch to see the current DB state — the task may have written "complete"
        # on a successful retry before the exception propagated here (defensive guard).
        run = run_repo.find_by_id(run.id)
        if run.status != "complete":
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            write_dlq_entry(
                db,
                model_id=run.model_id,
                reason="scan_failed",
                run_id=run.id,
                error_message=str(exc),
            )
            db.commit()


def reap_stale_runs(db: Session, stale_after_seconds: int) -> int:
    """Mark 'running' runs as 'failed' when started_at is older than stale_after_seconds.

    Returns the number of runs reaped.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)
    stale = RunRepository(db).stale_running(cutoff)
    for run in stale:
        run.status = "failed"
        run.completed_at = datetime.now(timezone.utc)
    if stale:
        db.commit()
    return len(stale)


def execute_scan(
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
    done_probes = ProbeResultRepository(db).done_probe_names_for(run_id)
    remaining = compute_remaining_probes(
        done_probes,
        probe_categories if probe_categories else DEFAULT_PROBE_CATEGORIES,
    )

    if not remaining:
        logger.info(f"Run {run_id}: all probes already complete, skipping scan")
        return {"probe_results_count": len(done_probes), "skipped": True}
    else:
        logger.info(f"Remaining probes: {remaining}")

    if model_name.startswith("huggingface/"):
        rpm_limit = settings.hf_rpm_limit
        env_overrides = {"HF_TOKEN": settings.hf_token}
    else:
        rpm_limit = settings.openrouter_rpm_limit
        env_overrides = {"OPENROUTER_API_KEY": settings.openrouter_api_key}

    with tempfile.TemporaryDirectory() as output_dir:
        config = build_garak_config(
            model_name=model_name,
            probe_categories=probe_categories,
            output_dir=output_dir,
            parallel_attempts=parallel_attempts_override or settings.garak_parallel_attempts,
            rpm_limit=rpm_limit,
            soft_probe_prompt_cap=probe_prompt_cap or settings.garak_soft_probe_prompt_cap,
            probe_spec_override=",".join(remaining),
        )

        jsonl_path = run_garak(
            config,
            env_overrides,
            timeout=scan_timeout_seconds or settings.garak_timeout_seconds,
        )

        run = RunRepository(db).find_by_id(run_id)
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

        model = ModelRepository(db).find_by_id(run.model_id)
        if model is not None:
            model.last_scan_at = datetime.now(timezone.utc)
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


def write_dlq_entry(
    db: Session,
    model_id: uuid.UUID,
    reason: str,
    run_id: uuid.UUID | None = None,
    missing_categories: str | None = None,
    error_message: str | None = None,
) -> None:
    """Write a dead-letter queue entry for a failed or incomplete scan."""
    logger.warning(
        "DLQ: model=%s reason=%s run=%s", model_id, reason, run_id
    )
    ScanDlqRepository(db).create(
        model_id=model_id,
        reason=reason,
        run_id=run_id,
        missing_categories=missing_categories,
        error_message=error_message,
    )


def sync_model_statuses(
    db: Session,
    openrouter_models: list[dict],
    hf_models: list[dict],
) -> dict:
    """Upsert top models from OpenRouter and HF into the DB; archive models that
    dropped off the top list.

    Returns dict with upserted and archived counts.
    """
    model_repo = ModelRepository(db)
    discovered_names: set[str] = set()

    if not openrouter_models and not hf_models:
        logger.warning(
            "sync_model_statuses: no models returned from any source — "
            "skipping archive step to avoid incorrectly archiving all models"
        )
        return {"upserted": 0, "archived": 0}

    for model_data in openrouter_models:
        raw_id = model_data.get("id", "")
        if not raw_id:
            continue
        name = raw_id if raw_id.startswith("openrouter/") else f"openrouter/{raw_id}"
        parts = name.split("/")
        provider = parts[1] if len(parts) > 1 else name
        model_repo.upsert_from_source(name, provider, "openrouter")
        discovered_names.add(name)

    for model_data in hf_models:
        raw_id = model_data.get("id", "")
        if not raw_id:
            continue
        name = raw_id if raw_id.startswith("huggingface/") else f"huggingface/{raw_id}"
        parts = name.split("/")
        provider = parts[1] if len(parts) > 1 else name
        model_repo.upsert_from_source(name, provider, "hf")
        discovered_names.add(name)

    upserted = len(discovered_names)

    # Archive non-manual active models no longer in the top list.
    # Only archive from a source when that source returned results — if a fetch
    # fails and returns [] we must not archive all models from that source.
    active_source_clauses = []
    if openrouter_models:
        active_source_clauses.append(Model.source == "openrouter")
    if hf_models:
        active_source_clauses.append(Model.source == "hf")

    archived_count = 0
    if active_source_clauses:
        to_archive = (
            db.query(Model)
            .filter(
                sa_or_(*active_source_clauses),
                Model.status == "active",
                Model.name.notin_(discovered_names),
            )
            .all()
        )
        for model in to_archive:
            model.status = "archived"
        archived_count = len(to_archive)

    db.commit()
    logger.info("sync_model_statuses: upserted=%d archived=%d", upserted, archived_count)
    return {"upserted": upserted, "archived": archived_count}


def queue_stale_models(db: Session, scan_ttl_days: int) -> dict:
    """Create pending runs for all ACTIVE models where last_scan_at is null or past TTL.

    Returns dict with queued and skipped counts.
    """
    staleness_cutoff = datetime.now(timezone.utc) - timedelta(days=scan_ttl_days)
    active_models = ModelRepository(db).list_active()
    queued = 0
    skipped = 0

    for model in active_models:
        active_run = (
            db.query(Run)
            .filter(Run.model_id == model.id, Run.status.in_(["pending", "running"]))
            .first()
        )
        if active_run:
            skipped += 1
            continue

        if model.last_scan_at is not None:
            scan_time = model.last_scan_at
            if scan_time.tzinfo is None:
                scan_time = scan_time.replace(tzinfo=timezone.utc)
            if scan_time >= staleness_cutoff:
                skipped += 1
                continue

        db.add(Run(model_id=model.id, triggered_by="scheduled", status="pending"))
        queued += 1

    db.commit()
    logger.info("queue_stale_models: queued=%d skipped=%d", queued, skipped)
    return {"queued": queued, "skipped": skipped}
