"""Celery tasks for GarakBoard worker."""

import io
import logging
import tempfile
from datetime import datetime, timedelta, timezone

import yaml

from celery import Task
from celery.exceptions import Retry
from sqlalchemy.exc import OperationalError

from garakboard.worker.celery_app import celery_app
from garakboard.worker.garak_runner import DEFAULT_PROBE_CATEGORIES, build_garak_config, compute_remaining_probes, run_garak
from garakboard.worker.openrouter_client import fetch_top_models
from garakboard.worker.rate_limiter import get_run_lock
from garakboard.ingest.jsonl_parser import ingest_jsonl_file
from garakboard.database import SessionLocal
from garakboard.models import Run, Model, ProbeResult, ProbeRunQueue
from garakboard.config import settings

logger = logging.getLogger(__name__)


class EmptyIngestError(Exception):
    """Raised when garak exits cleanly but produces no probe results or attempts."""


def publish_run_job(run_id: str, model_name: str, probe_categories: list[str]) -> None:
    """Publish a scan job to the Celery queue."""
    run_scan.delay(run_id, model_name, probe_categories)


@celery_app.task(
    bind=True,
    name="garakboard.worker.tasks.run_scan",
    max_retries=5,
    default_retry_delay=30,
)
def run_scan(self: Task, run_id: str, model_name: str, probe_categories: list[str]) -> dict:
    """
    Execute a garak scan for a given model and ingest results into the database.

    State transitions:
        pending → running (on task start)
        running → complete (on success)
        running → failed (on unrecoverable error)
        running → pending (on retry — model already running, or 429 from OpenRouter)

    Args:
        run_id: UUID string of the Run record in the database
        model_name: OpenRouter model name string
        probe_categories: List of probe category names to run

    Returns:
        dict with probe_results_count, attempts_count

    Raises:
        Retry: When another run is already active for this model, or on 429
    """
    db = SessionLocal()
    lock = get_run_lock()
    acquired = False

    try:
        # 1. Transition to running
        run = db.query(Run).filter(Run.id == run_id).first()
        if run is None:
            logger.error(f"Run {run_id} not found in database")
            return {"error": "run_not_found"}

        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        db.commit()

        # 2. Acquire per-model run lock — only one garak run per model at a time
        if not lock.acquire(model_name):
            logger.warning(f"Run already active for {model_name}, requeueing...")
            run.status = "pending"
            run.completed_at = None
            db.commit()
            raise self.retry(exc=Exception("Model already running"), countdown=30)
        acquired = True

        # 3. Compute which probes still need running (resume support)
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
            logger.info(f"Run {run_id}: all probes already complete, marking done")
            run.status = "complete"
            if run.completed_at is None:
                run.completed_at = datetime.now(timezone.utc)
            db.commit()
            return {"probe_results_count": len(done_probes), "skipped": True}

        # 4. Build garak config and run in temp directory
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

            try:
                jsonl_path = run_garak(config, settings.openrouter_api_key, timeout=settings.garak_timeout_seconds)
            except Exception as exc:
                # Check for 429 rate limit in exception message or garak's captured stdout/stderr
                exc_stdout = getattr(exc, "stdout", None)
                exc_stderr = getattr(exc, "stderr", None)
                stdout_str = exc_stdout.decode("utf-8", errors="replace") if isinstance(exc_stdout, bytes) else str(exc_stdout or "")
                stderr_str = exc_stderr.decode("utf-8", errors="replace") if isinstance(exc_stderr, bytes) else str(exc_stderr or "")
                exc_text = " ".join([str(exc), stdout_str, stderr_str])
                if "429" in exc_text or "rate limit" in exc_text.lower() or "rate-limited" in exc_text.lower():
                    logger.warning(f"Rate limit hit for run {run_id}, retrying...")
                    run.status = "pending"
                    run.completed_at = None
                    db.commit()
                    # Release lock before requeueing — no point holding the model slot
                    # during the countdown. The retried task will re-acquire.
                    acquired = False
                    lock.release(model_name)
                    countdown = 30 * (2 ** self.request.retries)
                    raise self.retry(exc=exc, countdown=countdown)

                # Unrecoverable garak error
                logger.error(f"garak failed for run {run_id}: {exc}")
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc)
                db.commit()
                return {"error": str(exc)}

            # 5. Store run metadata and raw JSONL before the temp dir is cleaned up
            try:
                import garak as _garak
                run.garak_version = _garak.__version__
                run.garak_config = yaml.dump(config, default_flow_style=False)
                with open(jsonl_path, "r", encoding="utf-8", errors="replace") as _jf:
                    run.raw_output = _jf.read()
                db.flush()
            except Exception as meta_exc:
                logger.warning(f"Failed to store run metadata for {run_id}: {meta_exc}")

            # 6. Ingest JSONL output (appended to any existing probe_results for this run)
            result = ingest_jsonl_file(io.StringIO(run.raw_output) if run.raw_output else jsonl_path, run_id, db)
            db.commit()

        # 7. Mark run as complete — but if garak exited 0 with an empty JSONL
        # (e.g. REST generator routing failure silently exits 0 with no output)
        # we treat that as a failure. No probe results AND no attempts is never
        # a legitimate success.
        if result.probe_results_count == 0 and result.attempts_count == 0:
            run.status = "failed"
            run.completed_at = datetime.now(timezone.utc)
            db.commit()
            raise EmptyIngestError(
                f"Run {run_id}: garak exited cleanly but produced zero probe results "
                "and zero attempts — possible generator or routing failure"
            )

        run.status = "complete"
        run.completed_at = datetime.now(timezone.utc)
        db.commit()

        logger.info(
            f"Run {run_id} complete: "
            f"{result.probe_results_count} probe results, "
            f"{result.attempts_count} attempts"
        )

        return {
            "probe_results_count": result.probe_results_count,
            "attempts_count": result.attempts_count,
        }

    except (Retry, EmptyIngestError):
        raise
    except OperationalError as exc:
        # Database connection error — retry with exponential backoff
        logger.warning(f"Database connection error in run_scan for {run_id}: {exc}")
        try:
            # Release lock before requeueing
            if acquired:
                lock.release(model_name)
                acquired = False
            # Reset any stuck ProbeRunQueue entries for this run's model
            db.rollback()
            run = db.query(Run).filter(Run.id == run_id).first()
            if run:
                run.status = "pending"
                run.completed_at = None
                db.commit()
                # Reset probe queue entries stuck in "running" status
                db.query(ProbeRunQueue).filter(
                    ProbeRunQueue.model_id == run.model_id,
                    ProbeRunQueue.status == "running",
                ).update({"status": "pending"})
                db.commit()
        except Exception as inner_exc:
            logger.error(
                f"Failed to reset run {run_id} after OperationalError: {inner_exc}"
            )
        countdown = 30 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=countdown)
    except Exception as exc:
        logger.error(f"Unexpected error in run_scan for {run_id}: {exc}")
        try:
            # Release lock before marking failed
            if acquired:
                lock.release(model_name)
                acquired = False
            db.rollback()
            run = db.query(Run).filter(Run.id == run_id).first()
            if run:
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc)
                db.commit()
                # Reset probe queue entries stuck in "running" status
                db.query(ProbeRunQueue).filter(
                    ProbeRunQueue.model_id == run.model_id,
                    ProbeRunQueue.status == "running",
                ).update({"status": "pending"})
                db.commit()
        except Exception as inner_exc:
            logger.error(
                f"Failed to mark run {run_id} as failed after error: {inner_exc}"
            )
        raise
    finally:
        if acquired:
            lock.release(model_name)
        db.close()


@celery_app.task(name="garakboard.worker.tasks.discover_and_schedule_scans")
def discover_and_schedule_scans() -> dict:
    """
    Discover the top-N free-tier models from OpenRouter and queue a scheduled
    scan for any model whose last complete run is older than scan_ttl_days.

    Returns:
        dict with 'queued' and 'skipped' counts.
    """
    if not settings.scheduler_enabled:
        return {"queued": 0, "skipped": 0}

    top_models = fetch_top_models(
        api_key=settings.openrouter_api_key,
        top_n=settings.scheduler_top_n_models,
        max_scan_cost_usd=settings.scheduler_max_scan_cost_usd,
    )

    db = SessionLocal()
    queued = 0
    skipped = 0

    try:
        staleness_cutoff = datetime.now(timezone.utc) - timedelta(days=settings.scheduler_scan_ttl_days)

        for model_data in top_models:
            openrouter_id = model_data.get("id", "")
            if not openrouter_id:
                continue

            # LiteLLM needs the "openrouter/" prefix to route through OpenRouter
            # rather than dispatching to the native provider (which rejects our key).
            model_name = (
                openrouter_id
                if openrouter_id.startswith("openrouter/")
                else f"openrouter/{openrouter_id}"
            )

            # Upsert model record (store the prefixed name that the worker actually uses)
            model = db.query(Model).filter(Model.name == model_name).first()
            if model is None:
                from datetime import date
                model = Model(
                    name=model_name,
                    provider=model_name.split("/")[1] if "/" in model_name else model_name,
                    snapshot_date=date.today(),
                )
                db.add(model)
                db.flush()

            # Check freshness: skip if a complete run exists within TTL
            latest_run = (
                db.query(Run)
                .filter(Run.model_id == model.id, Run.status == "complete")
                .order_by(Run.completed_at.desc())
                .first()
            )

            if latest_run and latest_run.completed_at:
                completed = latest_run.completed_at
                # Normalise to UTC-aware for comparison
                if completed.tzinfo is None:
                    completed = completed.replace(tzinfo=timezone.utc)
                if completed >= staleness_cutoff:
                    skipped += 1
                    continue

            # Queue a scheduled scan with the full default probe set
            run = Run(
                model_id=model.id,
                triggered_by="scheduled",
                status="pending",
            )
            db.add(run)
            db.commit()  # commit before publishing so the worker can find the run
            publish_run_job(str(run.id), model_name, DEFAULT_PROBE_CATEGORIES)
            queued += 1
    except Exception as exc:
        logger.error(f"discover_and_schedule_scans failed: {exc}")
        db.rollback()
        raise
    finally:
        db.close()

    logger.info(f"discover_and_schedule_scans: queued={queued}, skipped={skipped}")
    return {"queued": queued, "skipped": skipped}
