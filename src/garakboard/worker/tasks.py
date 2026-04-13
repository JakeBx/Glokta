"""Celery tasks for GarakBoard worker."""

import logging
import tempfile
from datetime import datetime, timezone

from celery import Task
from celery.exceptions import Retry

from garakboard.worker.celery_app import celery_app
from garakboard.worker.garak_runner import build_garak_config, run_garak
from garakboard.worker.rate_limiter import get_token_bucket
from garakboard.ingest.jsonl_parser import ingest_jsonl_file
from garakboard.database import SessionLocal
from garakboard.models import Run, Model
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
        running → pending (on 429 retry — Celery will re-queue)

    Args:
        run_id: UUID string of the Run record in the database
        model_name: OpenRouter model name string
        probe_categories: List of probe category names to run

    Returns:
        dict with probe_results_count, attempts_count

    Raises:
        Retry: On 429 rate limit responses (exponential backoff)
    """
    db = SessionLocal()
    try:
        # 1. Transition to running
        run = db.query(Run).filter(Run.id == run_id).first()
        if run is None:
            logger.error(f"Run {run_id} not found in database")
            return {"error": "run_not_found"}

        run.status = "running"
        run.started_at = datetime.now(timezone.utc)
        db.commit()

        # 2. Build garak config and run in temp directory
        with tempfile.TemporaryDirectory() as output_dir:
            config = build_garak_config(
                model_name=model_name,
                probe_categories=probe_categories,
                output_dir=output_dir,
                rpm_limit=settings.openrouter_rpm_limit,
                parallel_attempts=settings.garak_parallel_attempts,
            )

            # Acquire rate-limit token — retry with backoff if bucket is empty
            bucket = get_token_bucket(capacity=settings.openrouter_rpm_limit + 1)
            if not bucket.acquire(model_name):
                logger.warning(f"Rate limit bucket empty for {model_name}, retrying...")
                run.status = "pending"
                db.commit()
                countdown = 60  # wait for bucket to refill
                raise self.retry(exc=Exception("Rate limit bucket empty"), countdown=countdown)

            try:
                jsonl_path = run_garak(config, settings.openrouter_api_key)
            except Exception as exc:
                # Check for 429 rate limit error in exception message
                if "429" in str(exc) or "rate limit" in str(exc).lower():
                    logger.warning(f"Rate limit hit for run {run_id}, retrying...")
                    run.status = "pending"
                    db.commit()
                    countdown = 30 * (2 ** self.request.retries)
                    raise self.retry(exc=exc, countdown=countdown)

                # Unrecoverable garak error
                logger.error(f"garak failed for run {run_id}: {exc}")
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc)
                db.commit()
                return {"error": str(exc)}

            # 3. Ingest JSONL output
            result = ingest_jsonl_file(jsonl_path, run_id, db)
            db.commit()

        # 4. Mark run as complete
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
    except Exception as exc:
        logger.error(f"Unexpected error in run_scan for {run_id}: {exc}")
        try:
            run = db.query(Run).filter(Run.id == run_id).first()
            if run:
                run.status = "failed"
                run.completed_at = datetime.now(timezone.utc)
                db.commit()
        except Exception as inner_exc:
            logger.error(
                f"Failed to mark run {run_id} as failed after error: {inner_exc}"
            )
        raise
    finally:
        db.close()
