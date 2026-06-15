"""CTI ingest — centralised upsert that enforces the benchmark's temporal invariants.

All connectors funnel through here so the contamination-control rules live in one place:
- first_available_date anchored on max(input_date, label_date);
- mutable labels snapshotted (old row superseded, new row inserted) so scoring reproduces;
- newest-slice items flagged withhold within a recency window.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from glokta.infrastructure.cti.connectors.base import NormalisedCtiItem
from glokta.infrastructure.cti.connectors.cve import iter_cve_records, normalise_cve_record
from glokta.infrastructure.db.orm import CtiItem
from glokta.infrastructure.db.repos import CtiItemRepository

logger = logging.getLogger(__name__)


@dataclass
class UpsertOutcome:
    item: CtiItem
    action: str  # "created" | "snapshotted" | "unchanged"


def _first_available_date(
    input_date: date | None, label_date: date | None
) -> date | None:
    """Anchor = max(input_date, label_date); enrichment can lag publication by weeks."""
    dates = [d for d in (input_date, label_date) if d is not None]
    return max(dates) if dates else None


def upsert_item(
    db: Session,
    item: NormalisedCtiItem,
    *,
    now: date | None = None,
    withhold_window_days: int = 0,
) -> UpsertOutcome:
    """Upsert one normalised item, snapshotting on label change.

    Returns the live item and the action taken. The newest-slice withhold flag is set
    when the item's first_available_date falls within ``withhold_window_days`` of ``now``.
    """
    repo = CtiItemRepository(db)
    fad = _first_available_date(item.input_date, item.label_date)

    withhold = False
    if fad is not None and withhold_window_days > 0:
        today = now or datetime.now(timezone.utc).date()
        withhold = fad > today - timedelta(days=withhold_window_days)

    existing = repo.active_for(item.task, item.external_id)
    if existing is not None:
        if existing.label == item.label:
            # Refresh the holdout flag so an item graduates out of the rolling window once
            # it elapses (otherwise a once-withheld item stays withheld until its label changes).
            if existing.withhold != withhold:
                existing.withhold = withhold
                db.flush()
            return UpsertOutcome(existing, "unchanged")
        # Mutable label changed — snapshot: supersede the old, insert the new.
        existing.status = "superseded"
        db.flush()

    new = CtiItem(
        task=item.task,
        external_id=item.external_id,
        source=item.source,
        input_text=item.input_text,
        input_ref=item.input_ref,
        label=item.label,
        label_provenance=item.label_provenance,
        source_revision=item.source_revision,
        input_date=item.input_date,
        label_date=item.label_date,
        first_available_date=fad,
        authority_agreement=item.authority_agreement,
        difficulty=item.difficulty,
        withhold=withhold,
        status="active",
    )
    repo.add(new)
    action = "created" if existing is None else "snapshotted"
    return UpsertOutcome(new, action)


def ingest_items(
    db: Session,
    items: list[NormalisedCtiItem],
    *,
    now: date | None = None,
    withhold_window_days: int = 0,
) -> dict[str, int]:
    """Upsert a batch of normalised items; commit once. Returns action counts."""
    summary = {"created": 0, "snapshotted": 0, "unchanged": 0}
    for item in items:
        outcome = upsert_item(
            db, item, now=now, withhold_window_days=withhold_window_days
        )
        summary[outcome.action] += 1
    db.commit()
    logger.info("ingest_items: %s", summary)
    return summary


def run_cve_ingest(
    db: Session,
    root: str,
    source_revision: str | None,
    *,
    now: date | None = None,
    withhold_window_days: int = 0,
) -> dict[str, int]:
    """Walk a cvelistV5 checkout under ``root``, normalise every CVE, and ingest.

    The thin git pull + sha resolution lives in the connector (``git_sync``); this
    composes the tested normalise + ingest steps over the resulting files.
    """
    items: list[NormalisedCtiItem] = []
    for record in iter_cve_records(root):
        items.extend(normalise_cve_record(record, source_revision))
    return ingest_items(
        db, items, now=now, withhold_window_days=withhold_window_days
    )


def ingest_cve_records(
    db: Session,
    records: list[dict],
    source_revision: str | None,
    *,
    now: date | None = None,
    withhold_window_days: int = 0,
) -> dict[str, int]:
    """Normalise + ingest an in-memory list of CVE records (e.g. from the delta feed)."""
    items: list[NormalisedCtiItem] = []
    for record in records:
        items.extend(normalise_cve_record(record, source_revision))
    return ingest_items(
        db, items, now=now, withhold_window_days=withhold_window_days
    )
