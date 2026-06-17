"""Run-level CTI score aggregation (kept out of eval_service for testability)."""

import uuid
from datetime import date

from glokta.domain.cti.scoring import prequential_accuracy
from glokta.infrastructure.db.orm import CtiResult


def prequential_for_run(
    results: list[CtiResult],
    fad_by_item: dict[uuid.UUID, date | None],
    fading_factor: float,
) -> float | None:
    """Fading-factor accuracy over a run's results, ordered by item availability date.

    Results with no score are ignored; items missing an availability date sort oldest.
    Returns None when the run has no scored results.
    """
    series: list[tuple[date, float]] = []
    for r in results:
        if r.score is None:
            continue
        fad = fad_by_item.get(r.item_id) or date.min
        series.append((fad, r.score))
    return prequential_accuracy(series, fading_factor) if series else None


def auc_for_run(results: list[CtiResult]) -> float | None:
    """ROC AUC of forecast predictions vs resolved outcomes (Mann-Whitney form).

    Reads ``prob``/``outcome`` from each result's score_breakdown. Returns None when only
    one outcome class is present (AUC undefined). No sklearn dependency.
    """
    pairs: list[tuple[float, float]] = []
    for r in results:
        bd = r.score_breakdown or {}
        if "prob" in bd and "outcome" in bd:
            pairs.append((float(bd["prob"]), float(bd["outcome"])))

    positives = [p for p, o in pairs if o == 1.0]
    negatives = [p for p, o in pairs if o == 0.0]
    if not positives or not negatives:
        return None

    wins = 0.0
    for pos in positives:
        for neg in negatives:
            if pos > neg:
                wins += 1.0
            elif pos == neg:
                wins += 0.5
    return wins / (len(positives) * len(negatives))
