"""Client for fetching the top OpenRouter models ranked by actual token volume.

The public `/api/v1/models?order=...` endpoint silently ignores the `order` param
(verified 2026-04-30) and always returns the same default-sorted catalogue, so
we instead scrape the `rankingData` array embedded in the Server Components
payload of https://openrouter.ai/rankings — that is the same data the rankings
page itself renders. Pricing is then joined from the `/api/v1/models` catalogue
via `canonical_slug` ↔ `model_permaslug`.
"""

import json
import logging
import math
import re
from collections import defaultdict

import httpx

from glokta.config import settings

logger = logging.getLogger(__name__)


# Garak scan cost assumptions — must match src/glokta/config.py soft_probe_prompt_cap.
# These values are used to compute an expected USD cost per full scan so we can filter
# out models whose pricing would blow a per-model budget. The numbers are intentionally
# conservative; a real scan varies by ±30%.
_N_ACTIVE_PROBES = 91           # probes enabled by default in garak 0.14.1
_PROMPTS_PER_PROBE_CAP = 50     # glokta soft_probe_prompt_cap
_MULTI_TURN_OVERHEAD = 1.2      # jailbreak/tap/atkgen probes add ~20% extra attempts
_TOKENS_IN_PER_ATTEMPT = 250    # median input length across default probes
_TOKENS_OUT_PER_ATTEMPT = 300   # median output length (1 generation per prompt)


def estimate_scan_cost_usd(pricing: dict) -> float:
    """Estimate USD cost of a full garak scan for a model given its OpenRouter pricing.

    Pricing values are per-token (string decimals). BYOK models use "-1" for both
    prompt and completion; those can't be priced so we return +inf to guarantee
    they're excluded by any finite cap.
    """
    prompt_price = float(pricing.get("prompt", 0))
    completion_price = float(pricing.get("completion", 0))
    if prompt_price < 0 or completion_price < 0:
        return math.inf

    attempts = _N_ACTIVE_PROBES * _PROMPTS_PER_PROBE_CAP * _MULTI_TURN_OVERHEAD
    tokens_in = attempts * _TOKENS_IN_PER_ATTEMPT
    tokens_out = attempts * _TOKENS_OUT_PER_ATTEMPT
    return tokens_in * prompt_price + tokens_out * completion_price


def _fetch_rankings_data(timeout: float) -> list[dict]:
    """Scrape the `rankingData` array from the rankings page.

    The live HTML embeds the array as JSON-in-a-JS-string with backslash-escaped
    quotes (e.g. `\\"rankingData\\":[{\\"date\\":...}]`). If fetched with an
    `rsc: 1` header the payload comes back unescaped. We handle both, and we
    also need to unescape the per-record fields before JSON-parsing.
    """
    response = httpx.get(settings.openrouter_rankings_url, timeout=timeout)
    response.raise_for_status()
    text = response.text

    # Locate the start of the rankingData array in either form.
    start = text.find('\\"rankingData\\":[')
    escaped = start != -1
    if not escaped:
        start = text.find('"rankingData":[')
        if start == -1:
            logger.warning("No rankingData array found on OpenRouter rankings page")
            return []

    # Scan forward to find the matching closing bracket, respecting nested arrays.
    # Skip past the opening '[':
    open_bracket = text.index('[', start)
    depth = 1
    pos = open_bracket + 1
    while depth > 0 and pos < len(text):
        ch = text[pos]
        if escaped and ch == '\\' and pos + 1 < len(text) and text[pos + 1] in '[]"\\':
            pos += 2  # skip escape sequence
            continue
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
        pos += 1

    array_body = text[open_bracket + 1 : pos - 1]
    if escaped:
        # Collapse the backslash-escapes inside the JS string literal.
        array_body = array_body.replace('\\"', '"').replace('\\\\', '\\')

    records = []
    for obj_str in re.findall(r'\{[^{}]*\}', array_body):
        try:
            records.append(json.loads(obj_str))
        except json.JSONDecodeError:
            continue
    return records


def _rank_models_by_token_volume(records: list[dict]) -> list[str]:
    """Aggregate ranking records across dates/variants, return permaslugs sorted
    by total (prompt + completion) tokens descending.
    """
    totals: dict[str, int] = defaultdict(int)
    for r in records:
        slug = r.get("model_permaslug")
        if not slug:
            continue
        totals[slug] += r.get("total_prompt_tokens", 0) + r.get("total_completion_tokens", 0)

    return [slug for slug, _ in sorted(totals.items(), key=lambda kv: -kv[1])]


def _fetch_catalog_by_slug(api_key: str, timeout: float) -> dict[str, dict]:
    """Fetch /api/v1/models and return {canonical_slug: model_dict}."""
    response = httpx.get(
        settings.openrouter_catalog_url,
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        timeout=timeout,
    )
    response.raise_for_status()
    catalog = response.json().get("data", [])
    return {m.get("canonical_slug"): m for m in catalog if m.get("canonical_slug")}


def fetch_top_models(
    api_key: str,
    top_n: int,
    max_scan_cost_usd: float | None = 10.0,
    timeout: float = 30.0,
) -> list[dict]:
    """Return up to `top_n` OpenRouter models ranked by weekly token volume,
    filtered by per-model scan cost.

    Args:
        api_key: OpenRouter API key (used for catalog auth; rankings page is public).
        top_n: Maximum number of models to return.
        max_scan_cost_usd: Drop models whose estimated full-scan cost exceeds this
            amount. Default $10. Pass None to disable.

    Each returned dict is the raw catalog entry (includes `id`, `pricing`,
    `canonical_slug`, etc.) in ranked order.
    """
    ranking_records = _fetch_rankings_data(timeout)
    ranked_slugs = _rank_models_by_token_volume(ranking_records)
    catalog_by_slug = _fetch_catalog_by_slug(api_key, timeout)

    selected: list[dict] = []
    for slug in ranked_slugs:
        model = catalog_by_slug.get(slug)
        if model is None:
            continue  # ranked model no longer in catalog
        if max_scan_cost_usd is not None:
            cost = estimate_scan_cost_usd(model.get("pricing", {}))
            if cost > max_scan_cost_usd:
                continue
        selected.append(model)
        if len(selected) >= top_n:
            break

    return selected
