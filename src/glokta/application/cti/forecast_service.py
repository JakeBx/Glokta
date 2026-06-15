"""Forecast task plumbing: KEV upsert, forecast-item seeding, and label resolution.

Forecast items are seeded from CVEs at publication with a provisional ``exploited=False``
label; the answer is resolved later when (and if) the CVE enters CISA KEV. This makes the
task structurally leak-proof: the ground truth does not exist at submission time.

Forecast items are intentionally kept out of the generic mutable-label snapshot path —
re-ingesting a CVE never rewrites an already-seeded forecast item, and resolution updates
the label in place (the designed arrival of the answer, not a correction).

Maturity caveat: a not-yet-KEV item reads as a negative. Callers that want clean negatives
should restrict eval slices to items whose publication is older than a resolution window.
"""

import logging

from sqlalchemy.orm import Session

from glokta.infrastructure.cti.connectors.cve import _english_description, _parse_date
from glokta.infrastructure.cti.connectors.kev import KevEntry
from glokta.infrastructure.db.orm import CtiItem, CtiKev

logger = logging.getLogger(__name__)


def upsert_kev_entries(db: Session, entries: list[KevEntry]) -> int:
    """Insert KEV entries that aren't already stored; returns the number inserted."""
    existing = {row[0] for row in db.query(CtiKev.cve_id).all()}
    inserted = 0
    for entry in entries:
        if entry.cve_id in existing or entry.date_added is None:
            continue
        db.add(
            CtiKev(
                cve_id=entry.cve_id,
                date_added=entry.date_added,
                vendor=entry.vendor,
                product=entry.product,
            )
        )
        existing.add(entry.cve_id)
        inserted += 1
    db.commit()
    logger.info("upsert_kev_entries: inserted=%d", inserted)
    return inserted


def seed_forecast_items(
    db: Session,
    cve_records: list[dict],
    source_revision: str | None,
) -> int:
    """Create unresolved forecast items from CVE records; never duplicates. Returns count."""
    created = 0
    for record in cve_records:
        meta = record.get("cveMetadata") or {}
        cve_id = meta.get("cveId")
        if not cve_id:
            continue
        cna = (record.get("containers") or {}).get("cna") or {}
        description = _english_description(cna)
        if not description:
            continue
        exists = (
            db.query(CtiItem)
            .filter(
                CtiItem.task == "forecast",
                CtiItem.external_id == cve_id,
                CtiItem.status == "active",
            )
            .first()
        )
        if exists is not None:
            continue
        published = _parse_date(meta.get("datePublished"))
        db.add(
            CtiItem(
                task="forecast",
                external_id=cve_id,
                source="cve",
                input_text=description,
                label={"exploited": False},
                source_revision=source_revision,
                input_date=published,
                label_date=None,
                first_available_date=published,
                authority_agreement="single",
                difficulty={"description_length": len(description)},
                status="active",
            )
        )
        created += 1
    db.commit()
    logger.info("seed_forecast_items: created=%d", created)
    return created


def resolve_forecast_labels(db: Session, entries: list[KevEntry]) -> int:
    """Flip forecast items to exploited=True for CVEs now in KEV. Returns count resolved."""
    resolved = 0
    for entry in entries:
        item = (
            db.query(CtiItem)
            .filter(
                CtiItem.task == "forecast",
                CtiItem.external_id == entry.cve_id,
                CtiItem.status == "active",
            )
            .first()
        )
        if item is None or (item.label or {}).get("exploited") is True:
            continue
        item.label = {"exploited": True}
        item.label_date = entry.date_added
        resolved += 1
    db.commit()
    logger.info("resolve_forecast_labels: resolved=%d", resolved)
    return resolved
