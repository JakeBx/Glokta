"""SYN ingest (claim-set labels + reconstructed inputs) and the manual pilot entrypoint.

SYN is enabled in CTI_TASKS: the input-reconstruction leakage gate is enforced at ingest via the
hybrid masking policy (``ingest_syn_items(mask=True)`` masks the conclusion labels and drops
unmaskable residue), so the trigger queues it like any other task. ``run_syn_pilot`` remains a
manual entrypoint to spot-check SYN over a handful of items (e.g. against a new source).
"""

import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from glokta.application.cti.eval_service import execute_cti_run
from glokta.application.cti.ingest_service import ingest_items
from glokta.config import settings
from glokta.infrastructure.cti.claim_extraction import (
    build_claim_set,
    mask_conclusions,
    reconstruct_inputs,
)
from glokta.infrastructure.cti.connectors.base import NormalisedCtiItem
from glokta.infrastructure.cti.inference import complete
from glokta.infrastructure.db.orm import CtiRun
from glokta.infrastructure.db.repos import ModelRepository

logger = logging.getLogger(__name__)


def build_syn_item(
    advisory: dict,
    *,
    judge_infer=None,
    mask: bool = False,
    alias_index: dict[str, str] | None = None,
    min_input_chars: int = 200,
) -> NormalisedCtiItem | None:
    """Build a SYN item: reconstructed inputs as input_text, serialised claim set as label.

    With ``mask=True`` the hybrid leakage policy applies: ATT&CK technique ids and the actor
    name/aliases are masked out of the inputs (keeping behavioural evidence), and the advisory is
    dropped — returns None — if masking can't clear the residue (a conclusion claim still appears)
    or leaves the input below ``min_input_chars``.
    """
    inputs = reconstruct_inputs(advisory)
    if not inputs:
        return None  # nothing cleanly separable from conclusions — skip (pilot caveat)
    claim_set = build_claim_set(advisory, judge_infer=judge_infer)

    if mask:
        inputs = mask_conclusions(inputs, actor=advisory.get("actor"), alias_index=alias_index)
        if len(inputs) < min_input_chars:
            return None  # too little observation left after masking — drop
        lowered = inputs.lower()
        conclusion_values = [
            c.value for c in claim_set.claims if c.type in ("actor", "technique")
        ]
        if any(str(v).lower() in lowered for v in conclusion_values):
            return None  # residual leak masking couldn't clear — drop the advisory

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
    mask: bool = False,
    alias_index: dict[str, str] | None = None,
    withhold_window_days: int = 0,
) -> dict[str, int]:
    """Build + ingest SYN items from parsed advisories. Returns ingest action counts.

    ``mask=True`` applies the leakage-masking + drop-residue policy (see build_syn_item).
    """
    items = []
    for advisory in advisories:
        item = build_syn_item(
            advisory, judge_infer=judge_infer, mask=mask, alias_index=alias_index
        )
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
    """Manually evaluate SYN over a small slice — a spot-check entrypoint (SYN runs in production).

    Creates a one-off SYN run and scores it; inspect the resulting cti_results + the items'
    reconstructed inputs vs claim sets (e.g. when validating a newly added source).
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
