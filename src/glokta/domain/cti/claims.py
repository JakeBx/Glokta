"""SYN claim-set structures and reference-based scoring.

CTI-SYN scores a model's free-text threat assessment against an analyst product (a CISA
advisory) decomposed into a checkable claim set, on three axes:

- recall: did the model surface CISA's claims? (objective backbone)
- faithfulness/precision: are the model's claims grounded in the *reconstructed inputs*
  rather than hallucinated? (judge-assisted; the CTI-critical metric)
- calibration: did the model hedge where CISA hedged? (objective over matched claims)

Scoring is pure: the faithfulness judge is injected as a callable so this stays testable.
"""

from dataclasses import dataclass, field
from typing import Callable, Literal

ClaimType = Literal["actor", "cve", "technique", "sector", "ioc", "mitigation"]

# Faithfulness is weighted highest — a confidently-wrong CTI claim is worse than a miss.
_METRIC_WEIGHTS = {"recall": 1.0, "faithfulness": 2.0, "calibration": 1.0}


@dataclass
class Claim:
    type: ClaimType
    value: str
    hedge_level: float = 0.0  # 0.0 = asserted, 1.0 = fully hedged


@dataclass
class ClaimSet:
    claims: list[Claim] = field(default_factory=list)


def _canonical(value: str, ctype: str, alias_index: dict[str, str] | None) -> str:
    v = value.strip()
    if ctype in ("cve", "cwe", "technique"):
        return v.upper()
    if ctype == "actor":
        return (alias_index or {}).get(v.lower(), v)
    return v.casefold()


def score_syn(
    model_claims: ClaimSet,
    label_claims: ClaimSet,
    *,
    inputs: str = "",
    judge: Callable[[Claim, str], bool] | None = None,
    alias_index: dict[str, str] | None = None,
) -> tuple[float, dict]:
    """Score a model claim set against the label claim set. Returns (primary, breakdown)."""
    labels = label_claims.claims
    models = model_claims.claims

    # --- recall + matched pairs (for calibration) ---
    matched = 0
    hedge_diffs: list[float] = []
    for lc in labels:
        lc_key = _canonical(lc.value, lc.type, alias_index)
        match = next(
            (
                mc
                for mc in models
                if mc.type == lc.type
                and _canonical(mc.value, mc.type, alias_index) == lc_key
            ),
            None,
        )
        if match is not None:
            matched += 1
            hedge_diffs.append(abs(match.hedge_level - lc.hedge_level))

    recall = matched / len(labels) if labels else 1.0

    # --- faithfulness (judge-assisted) ---
    faithfulness: float | None = None
    if judge is not None and models:
        grounded = [1.0 if judge(mc, inputs) else 0.0 for mc in models]
        faithfulness = sum(grounded) / len(grounded)

    # --- calibration (objective over matched pairs) ---
    calibration: float | None = None
    if hedge_diffs:
        calibration = 1.0 - sum(hedge_diffs) / len(hedge_diffs)

    breakdown = {
        "recall": recall,
        "faithfulness": faithfulness,
        "calibration": calibration,
        "matched": matched,
        "label_count": len(labels),
        "model_count": len(models),
    }

    metrics = {"recall": recall, "faithfulness": faithfulness, "calibration": calibration}
    present = [(v, _METRIC_WEIGHTS[k]) for k, v in metrics.items() if v is not None]
    primary = (
        sum(v * w for v, w in present) / sum(w for _, w in present) if present else 0.0
    )
    return primary, breakdown
