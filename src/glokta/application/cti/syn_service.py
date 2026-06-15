"""SYN ingest (claim-set labels + reconstructed inputs) and the gated pilot entrypoint.

SYN stays disabled in CTI_TASKS, so the trigger never auto-queues it. ``run_syn_pilot`` is a
manual entrypoint to evaluate SYN over a handful of items for the input-reconstruction leakage
gate before SYN is enabled.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from glokta.application.cti.eval_service import execute_cti_run
from glokta.application.cti.ingest_service import ingest_items
from glokta.config import settings
from glokta.infrastructure.cti.claim_extraction import build_claim_set, reconstruct_inputs
from glokta.infrastructure.cti.connectors.base import NormalisedCtiItem
from glokta.infrastructure.cti.inference import complete
from glokta.infrastructure.db.orm import CtiRun
from glokta.infrastructure.db.repos import ModelRepository

logger = logging.getLogger(__name__)


def build_syn_item(advisory: dict, *, judge_infer=None) -> NormalisedCtiItem | None:
    """Build a SYN item: reconstructed inputs as input_text, serialised claim set as label."""
    inputs = reconstruct_inputs(advisory)
    if not inputs:
        return None  # nothing cleanly separable from conclusions — skip (pilot caveat)
    claim_set = build_claim_set(advisory, judge_infer=judge_infer)
    label = {
        "claims": [
            {"type": c.type, "value": c.value, "hedge_level": c.hedge_level}
            for c in claim_set.claims
        ]
    }
    published = advisory.get("published")
    return NormalisedCtiItem(
        task="syn",
        external_id=advisory["id"],
        source="report",
        input_text=inputs,
        label=label,
        input_ref={"sections": advisory.get("sections")},
        input_date=published,
        label_date=published,
        difficulty={"input_length": len(inputs)},
    )


def ingest_syn_items(
    db: Session,
    advisories: list[dict],
    *,
    judge_infer=None,
    withhold_window_days: int = 0,
) -> dict[str, int]:
    """Build + ingest SYN items from parsed advisories. Returns ingest action counts."""
    items = []
    for advisory in advisories:
        item = build_syn_item(advisory, judge_infer=judge_infer)
        if item is not None:
            items.append(item)
    return ingest_items(db, items, withhold_window_days=withhold_window_days)


def run_syn_pilot(
    db: Session,
    model_name: str,
    *,
    limit: int = 5,
    infer=complete,
    judge=None,
) -> dict:
    """Manually evaluate SYN over a small slice for the leakage-check gate (SYN stays disabled).

    Creates a one-off SYN run and scores it; inspect the resulting cti_results + the items'
    reconstructed inputs vs claim sets to decide whether SYN is safe to enable.
    """
    model = ModelRepository(db).find_by_name(model_name)
    if model is None:
        raise ValueError(f"Model {model_name!r} not found")
    run = CtiRun(
        model_id=model.id,
        task="syn",
        triggered_by="pilot",
        status="running",
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    db.flush()

    result = execute_cti_run(
        str(run.id), model_name, "syn", db, infer=infer, max_items=limit, judge=judge
    )
    run.status = "complete"
    run.completed_at = datetime.now(timezone.utc)
    db.commit()
    logger.info("run_syn_pilot: model=%s %s", model_name, result)
    return result
