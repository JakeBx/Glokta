"""Pure scoring functions for the CTI benchmark — no DB or network I/O.

Each scorer returns ``(primary_score, breakdown)`` where ``primary_score`` is the
scalar persisted on ``cti_result.score`` (higher is better, except Brier which is a
loss) and ``breakdown`` is the per-metric detail persisted on
``cti_result.score_breakdown``.
"""

from datetime import date
from typing import Any

from cvss import CVSS3  # type: ignore[import-untyped]

# CVSS v3.x base metrics (excludes Temporal/Environmental modifiers).
_CVSS3_BASE_METRICS = ("AV", "AC", "PR", "UI", "S", "C", "I", "A")


def _normalise_ids(ids: set[str]) -> set[str]:
    """Upper-case and strip identifiers so 'cwe-79' == ' CWE-79 '."""
    return {i.strip().upper() for i in ids if i and i.strip()}


def _set_f1(predicted: set[str], labels: set[str]) -> tuple[float, dict[str, float]]:
    """F1 over two normalised ID sets. Both empty -> perfect; one empty -> 0.0."""
    pred = _normalise_ids(predicted)
    gold = _normalise_ids(labels)

    if not pred and not gold:
        return 1.0, {"precision": 1.0, "recall": 1.0, "tp": 0, "fp": 0, "fn": 0}

    tp = len(pred & gold)
    fp = len(pred - gold)
    fn = len(gold - pred)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    return f1, {
        "precision": precision,
        "recall": recall,
        "tp": tp,
        "fp": fp,
        "fn": fn,
    }


def score_rcm(predicted: set[str], labels: set[str]) -> tuple[float, dict[str, float]]:
    """Score predicted CWE ids against label CWE ids by set F1 (RCM)."""
    return _set_f1(predicted, labels)


def score_ate(predicted: set[str], labels: set[str]) -> tuple[float, dict[str, float]]:
    """Score predicted ATT&CK technique ids against labels by set F1 (ATE)."""
    return _set_f1(predicted, labels)


def score_taa(
    predicted: str | None,
    label: str,
    alias_index: dict[str, str],
    related_index: dict[str, set[str]],
) -> tuple[float, dict[str, str | None]]:
    """Synonym-aware threat-actor scoring (athenabench C/P/I credit scheme).

    - 1.0 ("C"): predicted resolves to the same canonical actor as the label (alias match);
    - 0.5 ("P"): predicted is a *related* group of the label (plausible);
    - 0.0 ("I"): no connection.

    ``alias_index`` maps lower-cased alias/canonical -> canonical; ``related_index`` maps
    canonical -> set of related canonicals.
    """
    def canonical(name: str | None) -> str | None:
        if not name:
            return None
        key = name.strip().lower()
        return alias_index.get(key, name.strip())

    pred_c = canonical(predicted)
    label_c = canonical(label)

    if pred_c is None or label_c is None:
        result = "I"
    elif pred_c == label_c:
        result = "C"
    elif pred_c in related_index.get(label_c, set()) or label_c in related_index.get(
        pred_c, set()
    ):
        result = "P"
    else:
        result = "I"

    score = {"C": 1.0, "P": 0.5, "I": 0.0}[result]
    return score, {"result": result, "pred_canonical": pred_c, "label_canonical": label_c}


def _parse_cvss3(vector: str | None) -> CVSS3 | None:
    if not vector:
        return None
    try:
        return CVSS3(vector)
    except Exception:
        return None


def score_vsp(
    predicted_vector: str | None,
    label_vector: str,
    denominator: float = 7.7,
) -> tuple[float, dict[str, Any]]:
    """Score a predicted CVSS v3.1 vector against the label vector.

    Primary score (athenabench convention): ``1 - MAD/denominator`` over base scores,
    clamped to [0, 1]. Breakdown adds severity-band agreement and the fraction of
    differing base metrics. An unparseable/None prediction is scored against the full
    label base score (maximum deviation).
    """
    label = _parse_cvss3(label_vector)
    if label is None:
        raise ValueError(f"Unparseable label CVSS vector: {label_vector!r}")
    label_base = float(label.base_score)
    label_sev = label.severities()[0]

    pred = _parse_cvss3(predicted_vector)
    if pred is None:
        mad = label_base
        score = max(0.0, 1.0 - mad / denominator)
        return score, {
            "pred_base": None,
            "label_base": label_base,
            "mad": mad,
            "severity_match": False,
            "component_distance": 1.0,
        }

    pred_base = float(pred.base_score)
    mad = abs(pred_base - label_base)
    score = max(0.0, 1.0 - mad / denominator)

    differing = sum(
        1 for m in _CVSS3_BASE_METRICS if pred.metrics.get(m) != label.metrics.get(m)
    )
    return score, {
        "pred_base": pred_base,
        "label_base": label_base,
        "mad": mad,
        "severity_match": pred.severities()[0] == label_sev,
        "component_distance": differing / len(_CVSS3_BASE_METRICS),
    }


def score_forecast(predicted_prob: float, resolved: bool) -> tuple[float, dict[str, Any]]:
    """Per-item Brier score for the exploitation forecast (lower is better).

    ``brier = (clamp(prob) - outcome)^2`` with outcome 1.0 if later resolved by KEV.
    Run-level AUC is aggregated separately in the eval service.
    """
    prob = min(1.0, max(0.0, predicted_prob))
    outcome = 1.0 if resolved else 0.0
    brier = (prob - outcome) ** 2
    return brier, {"prob": prob, "outcome": outcome, "brier": brier}


def prequential_accuracy(
    results: list[tuple[date, float]],
    fading_factor: float = 0.99,
) -> float:
    """Gama et al. fading-factor accuracy over a recency-ordered score series.

    ``results`` is a list of ``(date, score in [0, 1])``; ordering is enforced here.
    The most recent item carries weight ``alpha**0`` and each older item decays by an
    extra factor of ``alpha``. ``fading_factor == 1.0`` reduces to the plain mean.
    """
    if not results:
        return 0.0

    ordered = sorted(results, key=lambda r: r[0])
    n = len(ordered)
    weighted_sum = 0.0
    weight_total = 0.0
    for i, (_, score) in enumerate(ordered):
        weight = fading_factor ** (n - 1 - i)
        weighted_sum += weight * score
        weight_total += weight

    return weighted_sum / weight_total if weight_total else 0.0
