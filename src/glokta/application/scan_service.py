"""Pure scan orchestration logic — no Prefect dependency."""

import io
import json
import logging
import tempfile
from datetime import date, datetime, timedelta, timezone
from typing import Callable

import yaml
from sqlalchemy.orm import Session

from glokta.config import settings
from glokta.application.ingest import ingest_jsonl_file
from glokta.infrastructure.db.orm import Model, Run
from glokta.infrastructure.db.repos import ModelRepository, ProbeResultRepository, RunRepository
from glokta.infrastructure.garak.runner import (
    DEFAULT_PROBE_CATEGORIES,
    build_garak_config,
    compute_remaining_probes,
    run_garak,
)
from glokta.domain.risks import ACTIVE_RISKS
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
    except Exception:
        # Re-fetch to see the current DB state — the task may have written "complete"
        # on a successful retry before the exception propagated here (defensive guard).
        run = run_repo.find_by_id(run.id)
        if run.status != "complete":
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
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


def queue_models_from_source(
    db: Session,
    model_dicts: list[dict],
    prefix: str,
    staleness_cutoff: datetime,
) -> tuple[int, int]:
    """Create pending runs for stale models from a single discovery source.

    Returns (queued_count, skipped_count).
    """
    model_repo = ModelRepository(db)
    queued = 0
    skipped = 0

    for model_data in model_dicts:
        raw_id = model_data.get("id", "")
        if not raw_id:
            continue

        model_name = raw_id if raw_id.startswith(prefix) else f"{prefix}{raw_id}"

        model = model_repo.find_by_name(model_name)
        if model is None:
            parts = model_name.split("/")
            provider = parts[1] if len(parts) > 1 else model_name
            model = Model(
                name=model_name,
                provider=provider,
                snapshot_date=date.today(),
            )
            db.add(model)
            db.flush()

        active = (
            db.query(Run)
            .filter(Run.model_id == model.id, Run.status.in_(["pending", "running"]))
            .first()
        )
        if active:
            skipped += 1
            continue

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

    return queued, skipped


def discover_and_queue(
    db: Session,
    api_key: str,
    top_n: int,
    max_scan_cost_usd: float,
    scan_ttl_days: int,
    hf_token: str = "",
    hf_top_n: int = 0,
) -> dict:
    """Fetch top models from OpenRouter (and optionally HuggingFace) and create
    pending runs for stale ones.

    Returns dict with queued and skipped counts.
    """
    staleness_cutoff = datetime.now(timezone.utc) - timedelta(days=scan_ttl_days)
    total_queued = 0
    total_skipped = 0

    or_models = fetch_top_models(
        api_key=api_key,
        top_n=top_n,
        max_scan_cost_usd=max_scan_cost_usd,
    )
    q, s = queue_models_from_source(db, or_models, "openrouter/", staleness_cutoff)
    total_queued += q
    total_skipped += s

    if hf_token and hf_top_n > 0:
        hf_models = fetch_top_hf_models(hf_token=hf_token, top_n=hf_top_n)
        q, s = queue_models_from_source(db, hf_models, "huggingface/", staleness_cutoff)
        total_queued += q
        total_skipped += s

    logger.info("discover_and_queue: queued=%d, skipped=%d", total_queued, total_skipped)
    return {"queued": total_queued, "skipped": total_skipped}


def queue_coverage_remediation(db: Session) -> dict:
    """Find all models whose latest complete run is missing one or more active risk
    categories, and queue a targeted run for the missing categories only.

    Returns a summary dict with counts and per-model details for verbose logging.
    """
    active_set = set(ACTIVE_RISKS)
    run_repo = RunRepository(db)
    pr_repo = ProbeResultRepository(db)

    models_with_runs = (
        db.query(Model)
        .join(Run, Run.model_id == Model.id)
        .filter(Run.status == "complete")
        .distinct()
        .all()
    )

    queued: list[dict] = []
    skipped_active: list[str] = []
    skipped_complete: list[str] = []
    errors: list[str] = []

    for model in models_with_runs:
        try:
            latest = (
                db.query(Run)
                .filter(Run.model_id == model.id, Run.status == "complete")
                .order_by(Run.completed_at.desc())
                .first()
            )
            if not latest:
                continue

            covered = pr_repo.covered_categories_for(latest.id)
            missing = sorted(active_set - covered)

            if not missing:
                skipped_complete.append(model.name)
                continue

            active = (
                db.query(Run)
                .filter(Run.model_id == model.id, Run.status.in_(["pending", "running"]))
                .first()
            )
            if active:
                skipped_active.append(model.name)
                logger.info(
                    "COVERAGE-REMEDIATION | SKIP | %s | already has active run %s",
                    model.name, str(active.id),
                )
                continue

            remediation_run = Run(
                model_id=model.id,
                triggered_by="scheduled",
                status="pending",
                probe_categories_json=json.dumps(missing),
            )
            db.add(remediation_run)
            db.flush()

            queued.append({
                "model": model.name,
                "run_id": str(remediation_run.id),
                "missing_categories": missing,
                "covered_categories": sorted(covered),
            })
            logger.info(
                "COVERAGE-REMEDIATION | QUEUED | %s | run=%s | missing=%s",
                model.name, str(remediation_run.id), missing,
            )

        except Exception as exc:
            errors.append(f"{model.name}: {exc}")
            logger.error("COVERAGE-REMEDIATION | ERROR | %s | %s", model.name, exc)

    db.commit()

    return {
        "queued": queued,
        "skipped_already_active": skipped_active,
        "skipped_full_coverage": skipped_complete,
        "errors": errors,
    }
