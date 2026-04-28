"""Thin client for the OpenRouter models API."""

import logging

import httpx

from garakboard.config import settings

logger = logging.getLogger(__name__)


def _is_free(model: dict) -> bool:
    pricing = model.get("pricing", {})
    prompt_price = str(pricing.get("prompt", "1"))
    return prompt_price == "0" or model.get("id", "").endswith(":free")


def fetch_top_models(api_key: str, top_n: int) -> list[dict]:
    """
    Fetch top_n free-tier models from OpenRouter sorted by context_length as a
    popularity proxy. Returns a list of model dicts each containing at least 'id'.
    """
    response = httpx.get(
        settings.openrouter_stats_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=30,
    )
    response.raise_for_status()

    data = response.json().get("data", [])
    free_models = [m for m in data if _is_free(m)]
    free_models.sort(key=lambda m: m.get("context_length", 0), reverse=True)

    return free_models[:top_n]
