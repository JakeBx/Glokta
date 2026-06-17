"""Per-item CTI evaluator — turns a raw model response into a scored result.

Pure (no DB/network): parse the response for the task, score it against the item label,
and produce a JSON-serialisable parsed_output. The eval service handles inference and
persistence; this keeps the parse+score logic independently testable.
"""

from dataclasses import dataclass

from glokta.domain.cti.scoring import (
    score_ate,
    score_forecast,
    score_rcm,
    score_taa,
    score_vsp,
)
from glokta.infrastructure.cti.prompts import parse_response


@dataclass
class ScoredResult:
    parsed_output: dict
    score: float
    breakdown: dict
    correct: bool


def _normalise_techniques(techniques: set[str], index: dict[str, str]) -> set[str]:
    """Resolve revoked technique ids to their current id where the index knows them."""
    return {index.get(t, t) for t in techniques}


def evaluate_item(
    task: str,
    label: dict,
    response: str,
    context: dict | None = None,
) -> ScoredResult:
    """Parse and score one model response for a task against its item label.

    ``context`` carries reference data some tasks need: ATE uses ``technique_index``
    (revoked->current); TAA uses ``alias_index`` and ``related_index``.
    """
    context = context or {}
    if task == "rcm":
        predicted: set[str] = parse_response("rcm", response)
        labels = set(label.get("cwe") or [])
        score, breakdown = score_rcm(predicted, labels)
        return ScoredResult(
            parsed_output={"cwe": sorted(predicted)},
            score=score,
            breakdown=breakdown,
            correct=score == 1.0,
        )

    if task == "vsp":
        vector: str | None = parse_response("vsp", response)
        score, breakdown = score_vsp(vector, label["vector"])
        return ScoredResult(
            parsed_output={"vector": vector},
            score=score,
            breakdown=breakdown,
            correct=bool(breakdown.get("severity_match")),
        )

    if task == "ate":
        predicted_t: set[str] = parse_response("ate", response)
        labels_t = set(label.get("techniques") or [])
        index = context.get("technique_index") or {}
        if index:
            predicted_t = _normalise_techniques(predicted_t, index)
            labels_t = _normalise_techniques(labels_t, index)
        score, breakdown = score_ate(predicted_t, labels_t)
        return ScoredResult(
            parsed_output={"techniques": sorted(predicted_t)},
            score=score,
            breakdown=breakdown,
            correct=score == 1.0,
        )

    if task == "taa":
        actor: str | None = parse_response("taa", response)
        taa_score, taa_breakdown = score_taa(
            actor,
            label.get("actor", ""),
            context.get("alias_index") or {},
            context.get("related_index") or {},
        )
        return ScoredResult(
            parsed_output={"actor": actor},
            score=taa_score,
            breakdown=dict(taa_breakdown),
            correct=taa_breakdown.get("result") == "C",
        )

    if task == "syn":
        from glokta.domain.cti.claims import Claim, ClaimSet, score_syn
        from glokta.infrastructure.cti.claim_extraction import claim_set_from_text

        alias_index = context.get("alias_index") or {}
        label_claims = ClaimSet(claims=[Claim(**c) for c in label.get("claims", [])])
        model_claims = claim_set_from_text(response, alias_index)
        primary, syn_breakdown = score_syn(
            model_claims,
            label_claims,
            inputs=context.get("inputs", ""),
            judge=context.get("judge"),
            alias_index=alias_index,
        )
        return ScoredResult(
            parsed_output={"claims": [{"type": c.type, "value": c.value} for c in model_claims.claims]},
            score=primary,
            breakdown=syn_breakdown,
            correct=syn_breakdown.get("recall") == 1.0,
        )

    if task == "forecast":
        prob: float | None = parse_response("forecast", response)
        resolved = bool(label.get("exploited"))
        prob_used = 0.5 if prob is None else prob
        brier, breakdown = score_forecast(prob_used, resolved)
        return ScoredResult(
            parsed_output={"prob": prob},
            score=1.0 - brier,  # higher-is-better, consistent with other tasks
            breakdown=breakdown,
            correct=(prob_used >= 0.5) == resolved,
        )

    raise ValueError(f"No evaluator for task {task!r}")
