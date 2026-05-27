"""Client for fetching top HuggingFace Inference API models ranked by downloads.

Uses huggingface_hub.HfApi.list_models() to discover text-generation models
with active serverless inference endpoints, sorted by download count descending.
"""

import logging
from typing import Any

from huggingface_hub import HfApi

logger = logging.getLogger(__name__)


def fetch_top_hf_models(
    hf_token: str,
    top_n: int,
) -> list[dict[str, Any]]:
    """Return up to `top_n` HuggingFace models with active serverless inference,
    ranked by downloads descending.

    Args:
        hf_token: HuggingFace API token (read scope sufficient).
        top_n:    Maximum number of models to return.

    Each returned dict contains at least an ``id`` key with the bare HF model ID
    (e.g. ``"meta-llama/Llama-3.1-8B-Instruct"`` — no ``huggingface/`` prefix).
    """
    api = HfApi(token=hf_token)
    raw = api.list_models(
        filter="text-generation",
        inference="warm",
        sort="downloads",
        limit=top_n,
    )

    results: list[dict[str, Any]] = []
    for model_info in raw:
        model_id = getattr(model_info, "id", None) or getattr(model_info, "modelId", None)
        if not model_id:
            logger.warning("HfApi returned a model with no id field; skipping")
            continue
        results.append({"id": model_id})

    logger.info("fetch_top_hf_models: found %d models", len(results))
    return results
