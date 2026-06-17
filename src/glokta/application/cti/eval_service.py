"""CTI evaluation orchestration — the structural parallel to scan_service.execute_scan.

Slices the active item pool for a task, runs the model over each item, scores via the
evaluator, tags pre/post-cutoff, and records a prequential aggregate on the run. Inference
is injectable so the orchestration is testable without network access.
"""

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Protocol

from sqlalchemy.orm import Session

from glokta.application.cti.scoring_aggregate import auc_for_run, prequential_for_run
from glokta.config import settings
from glokta.domain.cti.scoring import prequential_accuracy
from glokta.domain.cti.tasks import ENABLED_TASKS
from glokta.infrastructure.cti.evaluator import evaluate_item
from glokta.infrastructure.cti.inference import complete
from glokta.infrastructure.cti.prompts import build_prompt, prompt_hash
from glokta.infrastructure.llm.throttle import RateLimiter
from glokta.infrastructure.db.orm import CtiItem, CtiResult, CtiRun
from glokta.infrastructure.db.repos import (
    CtiItemRepository,
    CtiResultRepository,
    CtiRunRepository,
    ModelRepository,
)

logger = logging.getLogger(__name__)


class InferFn(Protocol):
    def __call__(self, model_name: str, prompt: str, *, api_key: str | None = ...) -> str: ...


def _build_context(db: Session, task: str) -> dict:
    """Assemble the reference data an evaluator needs for ate/taa/syn scoring."""
    if task == "ate":
        from glokta.application.cti.reference_service import build_technique_index

        return {"technique_index": build_technique_index(db)}
    if task == "taa":
        from glokta.application.cti.reference_service import build_taa_indices

        alias_index, related_index = build_taa_indices(db)
        return {"alias_index": alias_index, "related_index": related_index}
    if task == "syn":
        from glokta.application.cti.reference_service import build_taa_indices
        from glokta.infrastructure.cti.judge import make_grounding_judge

        alias_index, _ = build_taa_indices(db)
        return {"alias_index": alias_index, "judge": make_grounding_judge()}
    return {}


def execute_cti_run(
    run_id: str,
    model_name: str,
    task: str,
    db: Session,
    *,
    infer: InferFn = complete,
    api_key: str | None = None,
    fading_factor: float | None = None,
    max_items: int | None = None,
    commit_every: int | None = None,
    rpm_limit: int | None = None,
    judge: object | None = None,
) -> dict:
    """Run one model against one task's active item slice; persist scored results.

    Bounded + resumable: items already scored in this run are skipped, no more than
    ``max_items`` new items are scored, results are committed every ``commit_every`` items,
    and inference is paced to ``rpm_limit``.
    """
    max_items = max_items if max_items is not None else settings.cti_max_items_per_run
    commit_every = commit_every if commit_every is not None else settings.cti_eval_commit_every
    limiter = RateLimiter(rpm_limit if rpm_limit is not None else settings.cti_rpm_limit)

    run = CtiRunRepository(db).find_by_id(uuid.UUID(run_id))
    if run is None:
        raise ValueError(f"CTI run {run_id} not found")
    model = ModelRepository(db).find_by_id(run.model_id)
    if model is None:
        raise ValueError(f"Model {run.model_id} not found for CTI run {run_id}")

    # Snapshot the model's cutoff range onto the run for reproducible slicing.
    run.model_cutoff_start = model.cutoff_start
    run.model_cutoff_end = model.cutoff_end
    cutoff_end = model.cutoff_end

    # Exclude already-scored items BEFORE the cap so a resumed/capped run advances onto the
    # next page instead of re-seeing an already-scored first page.
    already_scored = CtiResultRepository(db).scored_item_ids_for(run.id)
    items = CtiItemRepository(db).slice_for_task(
        task, exclude_withheld=True, limit=max_items, exclude_ids=already_scored
    )
    context = _build_context(db, task)
    if task == "syn" and judge is not None:
        context["judge"] = judge

    new_count = 0
    for item in items:
        if new_count >= max_items:
            break
        limiter.wait()
        prompt = build_prompt(task, item.input_text or "")
        response = infer(model_name, prompt, api_key=api_key)
        eval_context = (
            {**context, "inputs": item.input_text or ""} if task == "syn" else context
        )
        scored = evaluate_item(task, item.label or {}, response, eval_context)
        pre_cutoff = (
            cutoff_end is not None
            and item.first_available_date is not None
            and item.first_available_date <= cutoff_end
        )
        db.add(
            CtiResult(
                run_id=run.id,
                item_id=item.id,
                model_id=model.id,
                prompt=prompt,
                prompt_hash=prompt_hash(prompt),
                response=response,
                parsed_output=scored.parsed_output,
                score=scored.score,
                score_breakdown=scored.breakdown,
                correct=scored.correct,
                pre_cutoff=pre_cutoff,
            )
        )
        new_count += 1
        if commit_every > 0 and new_count % commit_every == 0:
            db.commit()
    db.flush()

    ff = fading_factor if fading_factor is not None else settings.cti_prequential_fading_factor
    all_results = CtiResultRepository(db).for_run(run.id)
    # Anchor dates for ALL scored items (including ones scored on earlier attempts), so
    # prequential weighting is correct across resumes — not just for the current page.
    result_item_ids = {r.item_id for r in all_results}
    fad_by_item: dict[uuid.UUID, date | None] = (
        {
            iid: fad
            for iid, fad in db.query(
                CtiItem.id, CtiItem.first_available_date
            ).filter(CtiItem.id.in_(result_item_ids))
        }
        if result_item_ids
        else {}
    )
    run.item_count = len(all_results)
    run.scored_count = len(all_results)
    run.prequential_score = prequential_for_run(all_results, fad_by_item, ff)
    if task == "forecast":
        run.config = {**(run.config or {}), "auc": auc_for_run(all_results)}
    db.commit()

    logger.info(
        "execute_cti_run: run=%s task=%s items=%d new=%d prequential=%s",
        run_id, task, run.item_count, new_count, run.prequential_score,
    )
    return {
        "item_count": run.item_count,
        "scored_count": run.scored_count,
        "new": new_count,
    }


def process_pending_cti_run(db: Session, run_fn: Callable[[str, str, str], object]) -> None:
    """Claim one pending CTI run and execute it via run_fn(run_id, model_name, task).

    Mirrors scan_service.process_pending_run: status transitions, single-claim semantics.
    """
    run_repo = CtiRunRepository(db)
    run = run_repo.pending_one_locked()
    if run is None:
        return

    model = ModelRepository(db).find_by_id(run.model_id)
    if model is None:
        raise ValueError(f"Model {run.model_id} not found for CTI run {run.id}")

    run.status = "running"
    run.started_at = datetime.now(timezone.utc)
    db.commit()
    run_id = run.id

    try:
        run_fn(str(run_id), model.name, run.task)
        refreshed = run_repo.find_by_id(run_id)
        if refreshed is not None and refreshed.status != "complete":
            refreshed.status = "complete"
            refreshed.completed_at = datetime.now(timezone.utc)
            db.commit()
    except Exception as exc:
        refreshed = run_repo.find_by_id(run_id)
        if refreshed is not None and refreshed.status != "complete":
            refreshed.status = "failed"
            refreshed.completed_at = datetime.now(timezone.utc)
            db.commit()
        logger.error("process_pending_cti_run: run failed: %s", exc)
        raise


def queue_cti_runs(
    db: Session,
    tasks: list[str] | None = None,
    scan_ttl_days: int = 7,
) -> dict[str, int]:
    """Queue a pending CtiRun for each active model x task that is due.

    A (model, task) is skipped when a pending/running run already exists or a complete
    run finished within ``scan_ttl_days``. Mirrors scan_service.queue_stale_models.
    """
    task_list = tasks if tasks is not None else ENABLED_TASKS
    cutoff = datetime.now(timezone.utc) - timedelta(days=scan_ttl_days)
    run_repo = CtiRunRepository(db)
    active_models = ModelRepository(db).list_active()

    queued = 0
    skipped = 0
    for model in active_models:
        for task in task_list:
            if run_repo.has_active_run(model.id, task):
                skipped += 1
                continue
            last_complete = (
                db.query(CtiRun)
                .filter(
                    CtiRun.model_id == model.id,
                    CtiRun.task == task,
                    CtiRun.status == "complete",
                )
                .order_by(CtiRun.completed_at.desc())
                .first()
            )
            if last_complete is not None and last_complete.completed_at is not None:
                completed = last_complete.completed_at
                if completed.tzinfo is None:
                    completed = completed.replace(tzinfo=timezone.utc)
                if completed >= cutoff:
                    skipped += 1
                    continue
            db.add(
                CtiRun(model_id=model.id, task=task, triggered_by="scheduled", status="pending")
            )
            queued += 1

    db.commit()
    logger.info("queue_cti_runs: queued=%d skipped=%d", queued, skipped)
    return {"queued": queued, "skipped": skipped}


# prequential_accuracy is re-exported for callers/tests that aggregate manually.
__all__ = [
    "execute_cti_run",
    "process_pending_cti_run",
    "queue_cti_runs",
    "prequential_accuracy",
]
