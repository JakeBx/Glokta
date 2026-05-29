"""Client for fetching top HuggingFace Inference API models ranked by downloads.

Uses huggingface_hub.HfApi.list_models() to discover text-generation models
with active serverless inference endpoints, sorted by download count descending,
then cross-filters against the HF Inference Providers Router's supported model list
so only models scannable via router.huggingface.co are returned.
"""

import logging
from typing import Any

import requests
from huggingface_hub import HfApi

logger = logging.getLogger(__name__)

_HF_ROUTER_MODELS_URL = "https://router.huggingface.co/v1/models"


def _fetch_router_model_ids(hf_token: str) -> set[str]:
    """Return set of model IDs available via the HF Inference Providers Router."""
    resp = requests.get(
        _HF_ROUTER_MODELS_URL,
        headers={"Authorization": f"Bearer {hf_token}"},
        timeout=15,
    )
    resp.raise_for_status()
    return {m["id"] for m in resp.json().get("data", [])}


def fetch_top_hf_models(
    hf_token: str,
    top_n: int,
) -> list[dict[str, Any]]:
    """Return up to `top_n` HuggingFace models scannable via the HF Router,
    ranked by downloads descending.
    """
    overfetch = max(top_n * 10, 200)
    api = HfApi(token=hf_token)
    raw = api.list_models(
        filter="text-generation",
        inference="warm",
        sort="downloads",
        limit=overfetch,
    )

    try:
        router_ids = _fetch_router_model_ids(hf_token)
    except Exception as exc:
        logger.warning("Could not fetch Router model list (%s); skipping Router filter", exc)
        router_ids = None

    results: list[dict[str, Any]] = []
    for model_info in raw:
        if len(results) >= top_n:
            break
        model_id = getattr(model_info, "id", None) or getattr(model_info, "modelId", None)
        if not model_id:
            logger.warning("HfApi returned a model with no id field; skipping")
            continue
        if router_ids is not None and model_id not in router_ids:
            logger.debug("Skipping %s: not available via HF Router", model_id)
            continue
        results.append({"id": model_id})

    logger.info(
        "fetch_top_hf_models: found %d models (router_filter=%s)",
        len(results),
        router_ids is not None,
    )
    return results
