"""LLM-judge for the fuzzy SYN residue (claim faithfulness).

Only the genuinely-fuzzy faithfulness check is judge-mediated; recall and calibration keep
an objective backbone (see domain/cti/claims.py). The judge model and inference function are
injectable so this is testable without network access.
"""

import logging
from typing import Callable

from glokta.config import settings
from glokta.domain.cti.claims import Claim
from glokta.infrastructure.cti.inference import complete_via_openrouter

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = (
    "You are a CTI quality reviewer. Decide whether the following claim is grounded in the "
    "provided source inputs (i.e. supported by evidence in the inputs), versus hallucinated.\n\n"
    "Claim ({claim_type}): {claim_value}\n\n"
    "Source inputs:\n{inputs}\n\n"
    "Answer strictly YES (grounded) or NO (not grounded)."
)


def make_grounding_judge(
    *,
    infer: Callable[..., str] = complete_via_openrouter,
    model: str | None = None,
) -> Callable[[Claim, str], bool]:
    """Build a faithfulness judge ``judge(claim, inputs) -> bool``.

    The returned callable asks the judge model whether a claim is grounded in the inputs and
    parses a YES/NO answer (defaulting to not-grounded on an ambiguous reply). Inference defaults
    to ``complete_via_openrouter`` so the judge always routes through OpenRouter.
    """
    judge_model = model or settings.cti_judge_model

    def judge(claim: Claim, inputs: str) -> bool:
        prompt = _JUDGE_PROMPT.format(
            claim_type=claim.type, claim_value=claim.value, inputs=inputs
        )
        response = infer(judge_model, prompt)
        return response.strip().lower().startswith("yes")

    return judge
