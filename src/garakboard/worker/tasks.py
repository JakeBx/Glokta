"""Celery tasks for GarakBoard worker."""

import logging
import tempfile
from datetime import datetime, timezone

from celery import Task
from celery.exceptions import Retry
from sqlalchemy.exc import OperationalError

from garakboard.worker.celery_app import celery_app
from garakboard.worker.garak_runner import build_garak_config, run_garak
from garakboard.worker.rate_limiter import get_run_lock
from garakboard.ingest.jsonl_parser import ingest_jsonl_file
from garakboard.database import SessionLocal
from garakboard.models import Run, Model, ProbeRunQueue
from garakboard.config import settings

logger = logging.getLogger(__name__)


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
            db.commit()
            raise self.retry(exc=Exception("Model already running"), countdown=30)
        acquired = True

        # 3. Build garak config and run in temp directory
        with tempfile.TemporaryDirectory() as output_dir:
            config = build_garak_config(
                model_name=model_name,
                probe_categories=probe_categories,
                output_dir=output_dir,
                parallel_attempts=settings.garak_parallel_attempts,
                rpm_limit=settings.openrouter_rpm_limit,
                soft_probe_prompt_cap=settings.garak_soft_probe_prompt_cap,
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

            # 4. Ingest JSONL output
            result = ingest_jsonl_file(jsonl_path, run_id, db)
            db.commit()

        # 5. Mark run as complete
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

    except Retry:
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
