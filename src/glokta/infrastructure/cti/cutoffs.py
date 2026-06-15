"""Curated per-model training cutoff ranges.

Cutoffs are fuzzy and rarely disclosed, so each entry is a (start, end) *range* and the
values are best-effort estimates — they should be refined as vendors publish details. A model
with no match keeps null cutoffs and is treated as post-cutoff (conservative) by the eval
slicer. Matching is by case-insensitive substring of the glokta model name.
"""

import logging
from datetime import date

from sqlalchemy.orm import Session

from glokta.infrastructure.db.orm import Model

logger = logging.getLogger(__name__)

# (name substring, cutoff_start, cutoff_end). First match wins, so order specific -> general.
CUTOFFS: list[tuple[str, date, date]] = [
    ("claude-opus-4", date(2024, 9, 1), date(2025, 2, 1)),
    ("claude-sonnet-4", date(2024, 9, 1), date(2025, 2, 1)),
    ("claude-3.5", date(2024, 1, 1), date(2024, 4, 1)),
    ("llama-3.3", date(2023, 12, 1), date(2024, 6, 1)),
    ("llama-3.1", date(2023, 12, 1), date(2024, 3, 1)),
    ("qwen3", date(2024, 6, 1), date(2025, 1, 1)),
    ("deepseek-v3", date(2024, 1, 1), date(2024, 7, 1)),
    ("gpt-4o", date(2023, 10, 1), date(2024, 1, 1)),
    ("gemini-2", date(2024, 1, 1), date(2024, 8, 1)),
]


def cutoff_for(model_name: str) -> tuple[date, date] | None:
    """Return the (start, end) cutoff range for a model name, or None if unknown."""
    lname = model_name.lower()
    for substring, start, end in CUTOFFS:
        if substring in lname:
            return start, end
    return None


def apply_cutoffs(db: Session) -> int:
    """Set cutoff_start/end on models matching the curated map. Returns rows changed."""
    updated = 0
    for model in db.query(Model).all():
        match = cutoff_for(model.name)
        if match is None:
            continue
        start, end = match
        if model.cutoff_start != start or model.cutoff_end != end:
            model.cutoff_start = start
            model.cutoff_end = end
            updated += 1
    db.commit()
    logger.info("apply_cutoffs: updated=%d", updated)
    return updated
