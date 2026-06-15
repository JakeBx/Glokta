"""Client for fetching the top OpenRouter models ranked by weekly token volume.

Uses GET /api/v1/models?order=top-weekly, which OpenRouter now supports natively.
Pricing is available directly in the catalog response and used to filter by estimated
scan cost.
"""

import logging
import math

import httpx

from glokta.config import settings

logger = logging.getLogger(__name__)


# Garak scan cost assumptions — must match src/glokta/config.py soft_probe_prompt_cap.
_N_ACTIVE_PROBES = 91           # probes enabled by default in garak 0.14.1
_PROMPTS_PER_PROBE_CAP = 50     # glokta soft_probe_prompt_cap
_MULTI_TURN_OVERHEAD = 1.2      # jailbreak/tap/atkgen probes add ~20% extra attempts
_TOKENS_IN_PER_ATTEMPT = 250    # median input length across default probes
_TOKENS_OUT_PER_ATTEMPT = 300   # median output length (1 generation per prompt)


def estimate_scan_cost_usd(pricing: dict) -> float:
    """Estimate USD cost of a full garak scan for a model given its OpenRouter pricing."""
    prompt_price = float(pricing.get("prompt", 0))
    completion_price = float(pricing.get("completion", 0))
    if prompt_price < 0 or completion_price < 0:
        return math.inf

    attempts = _N_ACTIVE_PROBES * _PROMPTS_PER_PROBE_CAP * _MULTI_TURN_OVERHEAD
    tokens_in = attempts * _TOKENS_IN_PER_ATTEMPT
    tokens_out = attempts * _TOKENS_OUT_PER_ATTEMPT
    return tokens_in * prompt_price + tokens_out * completion_price


def fetch_top_models(
    api_key: str,
    top_n: int,
    max_scan_cost_usd: float | None = 10.0,
    timeout: float = 30.0,
) -> list[dict]:
    """Return up to `top_n` OpenRouter models ranked by weekly token volume,
    filtered by per-model scan cost.
    """
    response = httpx.get(
        settings.openrouter_catalog_url,
        params={"order": "top-weekly"},
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        timeout=timeout,
    )
    response.raise_for_status()
    catalog = response.json().get("data", [])

    selected: list[dict] = []
    for model in catalog:
        if not model.get("id"):
            continue
        elif model.get("id").endswith(":free"):
            continue
        if max_scan_cost_usd is not None:
            cost = estimate_scan_cost_usd(model.get("pricing", {}))
            if cost > max_scan_cost_usd:
                continue
        selected.append(model)
        if len(selected) >= top_n:
            break

    logger.info("fetch_top_models: returned %d of %d catalog entries", len(selected), len(catalog))
    return selected
